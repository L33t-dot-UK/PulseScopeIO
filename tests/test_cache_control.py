import json
from pathlib import Path
import shutil
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import urllib.request
import urllib.error
import numpy as np
from cache_control import CacheManager
from engine import ROOT
import server


class CacheControlTests(unittest.TestCase):
    def setUp(self):
        self.base = Path(tempfile.mkdtemp(prefix="cache-control-", dir=ROOT / "tmp"))
        self.root = self.base / ".cache"
        self.root.mkdir()
        self.manager = CacheManager(self.root)

    def tearDown(self):
        self.assertEqual(self.base.resolve().parent, (ROOT / "tmp").resolve())
        shutil.rmtree(self.base)

    def test_size_nested_files_and_repeat_empty_clear(self):
        (self.root / "a").write_bytes(b"a" * 123)
        (self.root / "nested").mkdir()
        (self.root / "nested/b").write_bytes(b"b" * 456)
        self.assertEqual(self.manager.info()["bytes"], 579)
        self.assertEqual(self.manager.info()["files"], 2)
        old_revision = self.manager.revision
        state = self.manager.clear({}, {})
        self.assertEqual(state["bytes"], 0)
        self.assertEqual(state["deleted_bytes"], 579)
        self.assertNotEqual(state["revision"], old_revision)
        self.assertTrue(state["complete"])
        self.assertTrue(self.manager.clear({}, {})["complete"])

    def test_shared_memmaps_closed_and_external_source_preserved(self):
        source = self.base / "original.csv"
        source.write_text("original source", encoding="utf8")
        mapped = np.memmap(
            self.root / "samples.bin", dtype="f8", mode="w+", shape=(16,)
        )
        mapped[:] = np.arange(16)
        a = SimpleNamespace(
            ready=True, error=None, data=mapped, edges={6: mapped[2:]}, source=source
        )
        b = SimpleNamespace(ready=True, error=None, data=mapped, replay_edges=mapped)
        jobs = {"a": a, "b": b}
        exports = {"pending-download": {"ready": True}}
        self.manager.clear(jobs, exports)
        self.assertTrue(mapped._mmap.closed)
        self.assertEqual(jobs, {})
        self.assertEqual(exports, {})
        self.assertEqual(source.read_text(), "original source")

    def test_active_request_and_unfinished_analysis_reject_deletion(self):
        item = self.root / "keep"
        item.write_bytes(b"keep")
        with self.manager.use():
            with self.assertRaisesRegex(ValueError, "in use"):
                self.manager.clear({}, {})
        with self.assertRaisesRegex(ValueError, "in use"):
            self.manager.clear({"active": SimpleNamespace(ready=False, error=None)}, {})
        self.assertEqual(item.read_bytes(), b"keep")

    def test_worker_lifetime_blocks_deletion(self):
        release = threading.Event()
        self.manager.start_worker(lambda: release.wait(5))
        try:
            self.assertGreater(self.manager.info()["busy"], 0)
            with self.assertRaisesRegex(ValueError, "in use"):
                self.manager.clear({}, {})
        finally:
            release.set()
        for _ in range(200):
            if self.manager.workers == 0:
                break
            time.sleep(0.005)
        self.assertEqual(self.manager.workers, 0)
        self.assertTrue(self.manager.clear({}, {})["complete"])

    def test_clear_blocks_new_readers_until_removal_finishes(self):
        (self.root / "job").mkdir()
        started, release = threading.Event(), threading.Event()
        remove = shutil.rmtree

        def delayed(path):
            started.set()
            release.wait(5)
            remove(path)

        with patch("cache_control.shutil.rmtree", side_effect=delayed):
            thread = threading.Thread(target=lambda: self.manager.clear({}, {}))
            thread.start()
            self.assertTrue(started.wait(2))
            try:
                with self.assertRaisesRegex(ValueError, "in progress"):
                    with self.manager.use():
                        pass
            finally:
                release.set()
                thread.join(5)
        self.assertFalse(thread.is_alive())

    def test_redirected_root_is_rejected(self):
        with patch("cache_control.linked", return_value=True):
            with self.assertRaisesRegex(ValueError, "redirected"):
                self.manager.clear({}, {})

    def test_linked_targets_not_counted_or_deleted(self):
        outside = self.base / "outside"
        outside.mkdir()
        (outside / "source").write_bytes(b"preserve")
        try:
            (self.root / "link").symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest(
                "Creating symlinks requires Windows Developer Mode or privilege"
            )
        self.assertEqual(self.manager.info()["bytes"], 0)
        self.assertTrue(self.manager.clear({}, {})["complete"])
        self.assertEqual((outside / "source").read_bytes(), b"preserve")

    def test_partial_failure_reports_remaining_size(self):
        folder = self.root / "held"
        folder.mkdir()
        (folder / "file").write_bytes(b"held")
        with patch(
            "cache_control.shutil.rmtree",
            side_effect=PermissionError("File busy in another process"),
        ):
            state = self.manager.clear({}, {})
        self.assertFalse(state["complete"])
        self.assertEqual(state["bytes"], 4)
        self.assertEqual(state["errors"][0]["name"], "held")

    def test_scan_failure_does_not_leave_cache_locked(self):
        with patch.object(self.manager, "scan", side_effect=OSError("scan failed")):
            with self.assertRaisesRegex(OSError, "scan failed"):
                self.manager.clear({}, {})
        self.assertFalse(self.manager.clearing)
        with self.manager.use():
            pass

    def test_cleanup_failure_invalidates_old_recordings(self):
        jobs = {"old": SimpleNamespace(ready=True, error=None)}
        exports = {"old-download": {}}
        revision = self.manager.revision
        with patch.object(
            self.manager, "close_recordings", side_effect=RuntimeError("close failed")
        ):
            state = self.manager.clear(jobs, exports)
        self.assertEqual(jobs, {})
        self.assertEqual(exports, {})
        self.assertNotEqual(state["revision"], revision)
        self.assertFalse(state["complete"])
        self.assertFalse(state["clearing"])

    def test_thread_start_failure_does_not_leave_a_queued_job(self):
        jobs = {}
        item = SimpleNamespace(id="failed", ready=False, error=None, run=lambda: None)
        with patch.multiple(
            server, cache_control=self.manager, LOCK=self.manager.lock, JOBS=jobs
        ), patch.object(server, "Recording", return_value=item), patch.object(
            threading.Thread, "start", side_effect=RuntimeError("thread start failed")
        ):
            with self.assertRaisesRegex(RuntimeError, "thread start failed"):
                server.launch("unused", {})
        self.assertEqual(item.error, "thread start failed")
        self.assertEqual(self.manager.workers, 0)
        self.assertTrue(self.manager.clear(jobs, {})["complete"])

    def test_http_controls_and_streaming_export_are_coordinated(self):
        (self.root / "cached").write_bytes(b"cached")
        export = self.root / "download.csv"
        export.write_bytes(b"export data")
        jobs = {"job": SimpleNamespace(id="job", ready=True, error=None)}
        exports = {
            "key": {"recording": "job", "ready": True, "path": export, "kind": "csv"}
        }
        with patch.multiple(
            server,
            cache_control=self.manager,
            LOCK=self.manager.lock,
            JOBS=jobs,
            EXPORTS=exports,
        ):
            http = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
            service = threading.Thread(target=http.serve_forever, daemon=True)
            service.start()
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

            def request(path, body=None, token=server.TOKEN):
                req = urllib.request.Request(
                    f"http://127.0.0.1:{http.server_port}" + path,
                    data=json.dumps(body).encode() if body is not None else None,
                    headers={
                        "X-Local-Token": token,
                        "Content-Type": "application/json",
                    },
                )
                with opener.open(req, timeout=10) as response:
                    return response.read()

            try:
                info = json.loads(request("/api/cache"))
                self.assertEqual(info["bytes"], 17)
                bad = urllib.request.Request(
                    f"http://127.0.0.1:{http.server_port}/api/clear-cache",
                    data=b"",
                    headers={"X-Local-Token": "invalid"},
                )
                with self.assertRaises(urllib.error.HTTPError) as err:
                    opener.open(bad, timeout=10)
                self.assertEqual(err.exception.code, 403)
                self.assertTrue((self.root / "cached").exists())
                started, release = threading.Event(), threading.Event()
                send = server.Handler.send_export
                downloaded = []

                def blocked(handler, path, kind):
                    started.set()
                    release.wait(5)
                    return send(handler, path, kind)

                with patch.object(server.Handler, "send_export", blocked):
                    client = threading.Thread(
                        target=lambda: downloaded.append(
                            request("/api/export-file?id=job&export=key")
                        )
                    )
                    client.start()
                    self.assertTrue(started.wait(2))
                    self.assertNotIn(
                        "key", exports
                    )  # token already consumed; lease still protects it
                    try:
                        with self.assertRaises(urllib.error.HTTPError) as err:
                            request("/api/clear-cache", {})
                        self.assertEqual(err.exception.code, 400)
                    finally:
                        release.set()
                        client.join(5)
                self.assertEqual(downloaded, [b"export data"])
                for _ in range(200):
                    if self.manager.users == 0:
                        break
                    time.sleep(0.005)
                state = json.loads(request("/api/clear-cache", {}))
                self.assertEqual(state["bytes"], 0)
                self.assertTrue(state["complete"])
                with self.assertRaises(urllib.error.HTTPError) as err:
                    request("/api/status?id=job")
                self.assertIn("Reload a recording", err.exception.read().decode())
            finally:
                http.shutdown()
                http.server_close()
