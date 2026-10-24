"""Loopback-only offline HTTP service with streaming import/export."""

import argparse, io, json, mimetypes, os, secrets, shutil, struct, threading, time, uuid, wave, webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs
from pathlib import Path
import numpy as np
from engine import Recording, ROOT, CACHE, NAMES, BLOCK, MAX_BYTES
from cache_control import CacheManager
from functools import wraps

JOBS = {}
EXPORTS = {}
cache_control = CacheManager(CACHE)
LOCK = cache_control.lock
TOKEN = uuid.uuid4().hex


def cache_guard(function):
    @wraps(function)
    def guarded(self):
        path = urlparse(self.path).path
        if not path.startswith("/api/") or path in (
            "/api/session",
            "/api/cache",
            "/api/clear-cache",
            "/api/browse",
        ):
            return function(self)
        try:
            with cache_control.use():
                return function(self)
        except ValueError as exc:
            return self.json({"error": str(exc)}, 409)

    return guarded


def recording(recording_id):
    if recording_id not in JOBS:
        raise ValueError("Recording cache is no longer available. Reload a recording.")
    return JOBS[recording_id]


def launch(path, options, second=None, third=None):
    with LOCK:
        if any(not r.ready and not r.error for r in JOBS.values()):
            raise ValueError("Wait for the current analysis or cancel it first")
        if second is not None:
            from merge import MergedRecording

            r = MergedRecording(path, second, options, path_c=third)
        else:
            r = Recording(path, options)
        JOBS[r.id] = r
    try:
        cache_control.start_worker(r.run)
    except Exception as exc:
        r.error = str(exc)
        r.stage = "Failed"
        raise
    return {"id": r.id}


def reanalyse(old, options):
    with LOCK:
        if any(not r.ready and not r.error for r in JOBS.values()):
            raise ValueError("Analysis already running")
        options = dict(options)
        if old.options.get("bearing_test"):
            options["bearing_test"] = old.options["bearing_test"]
        if old.options.get("binary_paths"):
            options["binary_paths"] = old.options["binary_paths"]
            options["bin_align_90"] = old.options.get("bin_align_90", False)
        if old.options.get("csv_channel_paths"):
            options["csv_channel_paths"] = old.options["csv_channel_paths"]
        r = Recording(old.source, options)
        for key in [
            "data",
            "count",
            "present",
            "seconds",
            "headers",
            "start",
            "end",
            "has_time",
            "source_time_unit",
            "time_heading",
        ]:
            setattr(r, key, getattr(old, key))
        if hasattr(old, "rate"):
            r.rate = old.rate
        for key in (
            "merge_info",
            "csv_columns",
            "csv_delimiter",
            "csv_headerless",
            "merge_paths",
            "display_name",
            "bin_alignment",
            "bin_timebase",
        ):
            if hasattr(old, key):
                value = getattr(old, key)
                # Each analysis owns its validation results; filter/threshold changes
                # must not leave a stale phase check or mutate an older readout.
                setattr(r, key, dict(value) if key == "merge_info" else value)
        JOBS[r.id] = r
    try:
        cache_control.start_worker(r.run)
    except Exception as exc:
        r.error = str(exc)
        r.stage = "Failed"
        raise
    return {"id": r.id}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def local_host(self):
        host = self.headers.get("Host", "").lower()
        return host in (
            f"127.0.0.1:{self.server.server_port}",
            f"localhost:{self.server.server_port}",
        )

    def json(self, obj, status=200):
        b = json.dumps(obj, allow_nan=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def reject_session(self):
        # Drain small JSON bodies before closing: on Windows an unread request
        # body can reset the connection before the client receives the 403.
        # Never wait indefinitely for an unauthenticated or oversized upload.
        timeout = self.connection.gettimeout()
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if 0 < size <= 65536:
                self.connection.settimeout(1)
                self.rfile.read(size)
        except (ValueError, OSError):
            pass
        finally:
            self.connection.settimeout(timeout)
        return self.json({"error": "Invalid local session token"}, 403)

    @cache_guard
    def do_POST(self):
        try:
            if not self.local_host():
                return self.json({"error": "Loopback host required"}, 403)
            if self.headers.get("X-Local-Token") != TOKEN:
                return self.reject_session()
            u = urlparse(self.path)
            q = parse_qs(u.query)
            size = int(self.headers.get("Content-Length", "0"))
            if u.path == "/api/upload":
                if size <= 0 or size > MAX_BYTES:
                    raise ValueError("Upload must be between 1 byte and 10 GiB")
                name = Path(q.get("name", ["capture.csv"])[0]).name
                folder = CACHE / ("upload-" + uuid.uuid4().hex)
                folder.mkdir()
                target = folder / name
                with open(target, "wb") as f:
                    remaining = size
                    while remaining:
                        chunk = self.rfile.read(min(4 * 1024 * 1024, remaining))
                        if not chunk:
                            raise ValueError("Upload interrupted")
                        f.write(chunk)
                        remaining -= len(chunk)
                return self.json(
                    launch(target, json.loads(q.get("options", ["{}"])[0]))
                )
            if size > 65536:
                raise ValueError("Request too large")
            body = json.loads(self.rfile.read(size) or b"{}")
            if u.path == "/api/clear-cache":
                return self.json(cache_control.clear(JOBS, EXPORTS))
            if u.path == "/api/prepare-export":
                r = recording(body["id"])
                if not r.ready:
                    raise ValueError("Recording is not ready")
                export_id = uuid.uuid4().hex
                state = {"recording": r.id, "ready": False, "error": None}
                EXPORTS[export_id] = state

                def prepare():
                    try:
                        state["path"], state["kind"] = Handler.prepare_export(r, body)
                        state["ready"] = True
                    except Exception as exc:
                        state["error"] = str(exc)

                cache_control.start_worker(prepare)
                return self.json({"export": export_id})
            if u.path == "/api/open":
                return self.json(launch(body["path"], body.get("options", {})))
            if u.path == "/api/open-csv-channels":
                paths=body.get("channels",{})
                if not isinstance(paths,dict) or not paths or len(paths)>6 or any(k not in NAMES or not isinstance(v,str) or not v.strip() for k,v in paths.items()):
                    raise ValueError("Select one to six named CSV channels")
                return self.json(launch(next(iter(paths.values())),dict(body.get("options",{}),csv_channel_paths=paths)))
            if u.path == "/api/open-bin":
                paths = body.get("channels", {})
                if (
                    not isinstance(paths, dict)
                    or not paths
                    or len(paths) > 6
                    or any(
                        k not in NAMES or not isinstance(v, str) or not v.strip()
                        for k, v in paths.items()
                    )
                ):
                    raise ValueError("Select one to six named BIN channels")
                return self.json(
                    launch(
                        next(iter(paths.values())),
                        dict(body.get("options", {}), binary_paths=paths),
                    )
                )
            if u.path == "/api/merge":
                return self.json(
                    launch(
                        body["path_a"],
                        body.get("options", {}),
                        body["path_b"],
                        body.get("path_c"),
                    )
                )
            if u.path == "/api/generate":
                from synthetic import generate_capture

                folder = CACHE / ("generated-" + uuid.uuid4().hex)
                kind = body.get("kind", "clean")
                metadata = generate_capture(
                    folder / (kind + ".csv"),
                    kind,
                    differential=bool(body.get("differential", True)),
                    seed=int(body.get("seed", secrets.randbits(32) if kind=="bearing" else 20261020)),
                )
                opts = body.get("options", {})
                opts.update(
                    low=2.5, high=2.5, auto=False, rpm="60", reference=kind in ("missing","bearing")
                )
                if kind in ("missing","bearing"):
                    opts.update(reference=True, ppr=8192)
                if kind=="bearing":
                    opts["bearing_test"]=metadata["bearing_error"]
                result = launch(metadata["path"], opts)
                result["generated"] = metadata
                return self.json(result)
            if u.path == "/api/browse":
                from file_picker import choose_file

                path = choose_file(body.get("kind"))
                return self.json({"path": path})
            if u.path == "/api/rpm":
                current = recording(body["id"])
                if not current.ready:
                    raise ValueError("Wait for analysis to finish")
                current.set_rpm(body.get("rpm", ""))
                return self.json(
                    {
                        "rpm": current.rpm,
                        "rpm_source": current.rpm_source,
                        "metrics": current.display_metrics(),
                        "diagnostics": current.diagnostics(),
                    }
                )
            if u.path == "/api/reanalyse":
                old = recording(body["id"])
                opts = body.get("options", {})
                if old.options.get("bearing_test"):
                    opts["bearing_test"] = old.options["bearing_test"]
                if old.options.get("csv_channel_paths"):
                    opts["csv_channel_paths"] = old.options["csv_channel_paths"]
                if not old.ready:
                    raise ValueError("Wait for import to finish")
                if not old.options.get("binary_paths") and (
                    any(
                        opts.get(k, "auto") != old.options.get(k, "auto")
                        for k in ["delimiter", "timecol"]
                    )
                    or (
                        getattr(old, "merge_paths", None)
                        and opts.get("alignment_mode", "check")
                        != old.options.get("alignment_mode", "check")
                    )
                ):
                    paths = getattr(old, "merge_paths", None)
                    return self.json(
                        launch(
                            old.source,
                            opts,
                            paths[1] if paths else None,
                            paths[2] if paths and len(paths) > 2 else None,
                        )
                    )
                return self.json(reanalyse(old, opts))
            if u.path == "/api/cancel":
                recording(body["id"]).cancelled = True
                return self.json({"ok": True})
            raise ValueError("Unknown operation")
        except Exception as e:
            self.json({"error": str(e)}, 400)

    @cache_guard
    def do_GET(self):
        try:
            if not self.local_host():
                return self.json({"error": "Loopback host required"}, 403)
            u = urlparse(self.path)
            q = {k: v[0] for k, v in parse_qs(u.query).items()}
            if u.path == "/api/session":
                return self.json({"token": TOKEN})
            if u.path == "/api/cache":
                return self.json(cache_control.info())
            if u.path.startswith("/api/"):
                r = recording(q["id"])
                if u.path == "/api/status":
                    return self.json(r.summary())
                if not r.ready:
                    raise ValueError("Recording is not ready")
                if u.path in ("/api/export-status", "/api/export-file"):
                    state = EXPORTS[q["export"]]
                    if state["recording"] != r.id:
                        raise ValueError("Export recording mismatch")
                    if u.path == "/api/export-status":
                        return self.json(
                            {"ready": state["ready"], "error": state["error"]}
                        )
                    if not state["ready"]:
                        raise ValueError(state["error"] or "Export is not ready")
                    if EXPORTS.pop(q["export"], None) is None:
                        raise ValueError("Export has already been downloaded")
                    return self.send_export(state["path"], state["kind"])
                if u.path == "/api/plot":
                    return self.json(
                        r.plot(q.get("start", r.start), q.get("end", r.end))
                    )
                if u.path == "/api/measurements":
                    return self.json(
                        r.span_measurements(
                            q.get("start", r.start), q.get("end", r.end)
                        )
                    )
                if u.path == "/api/pulse-counts":
                    return self.json(r.pulse_counts(q.get("offset", 0)))
                if u.path == "/api/position":
                    if "ppr" in q:
                        value = float(q["ppr"])
                        if not np.isfinite(value) or value <= 0:
                            raise ValueError("Replay pulses/rev must be positive")
                        r.replay_ppr = value
                    t = float(q.get("time", r.start))
                    if "advance" in q:
                        t = r.advance(t, max(0, min(60, float(q["advance"]))))
                    if "step" in q:
                        a = r.replay_edges
                        if len(a):
                            sample = (
                                int(np.searchsorted(r.data[:, 0], t, side="right")) - 1
                            )
                            index = int(np.searchsorted(a, sample, side="right")) - 1
                            index = max(-1, min(len(a) - 1, index + int(q["step"])))
                            t = r.start if index < 0 else float(r.data[a[index], 0])
                    return self.json(r.position(t))
                if u.path == "/api/next-fault":
                    return self.json(
                        {
                            "event": r.next_fault(
                                float(q["after_time"]) if "after_time" in q else None,
                                int(q.get("after_id", 0)),
                                q.get("kind"),
                                q.get("exclude_kind"),
                            )
                        }
                    )
                if u.path == "/api/export":
                    return self.export(r, q)
                raise ValueError("Unknown API")
            file = (
                ROOT / "output" / "pdf" / "Encoder_Pulse_Viewer_User_Guide.pdf"
                if u.path == "/user-guide.pdf"
                else ROOT
                / "static"
                / ({"/": "index.html", "/app.js": "app.js", "/health.js": "health.js", "/columns.js": "columns.js"}.get(u.path, ""))
            )
            if not file.is_file():
                self.send_error(404)
                return
            b = file.read_bytes()
            self.send_response(200)
            self.send_header(
                "Content-Type",
                mimetypes.guess_type(str(file))[0] or "application/octet-stream",
            )
            self.send_header("Content-Length", str(len(b)))
            self.send_header("Cache-Control", "no-store")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self' data:; script-src 'self' https://cdnjs.buymeacoffee.com; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com; img-src 'self' data: https://cdn.buymeacoffee.com https://cdnjs.buymeacoffee.com; connect-src 'self'; object-src 'none'; frame-ancestors 'none'",
            )
            self.end_headers()
            self.wfile.write(b)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:
            self.json({"error": str(e)}, 400)

    @staticmethod
    def prepare_export(r, q):
        kind = q.get("kind", "csv")
        refined = (
            getattr(r, "merge_info", {}).get("refinement", {}).get("applied", False)
        )
        renamed = kind == "csv" and "csv_headers" in q
        target = r.dir / (
            (("refined-" if refined else "") + "renamed-export-" if renamed else "refined-export-" if refined else "export-")
            + uuid.uuid4().hex
            + "."
            + kind
        )
        if renamed:
            from csv_export import write_csv
            write_csv(r,target,q["csv_headers"])
        elif (
            kind == "csv"
            and q.get("csv_mode", "original") == "original"
            and r.source.suffix.lower() not in (".wav", ".bin")
            and not r.options.get("csv_channel_paths")
            and not getattr(r, "merge_info", None)
        ):
            shutil.copyfile(r.source, target)
        elif kind == "csv":
            with open(target, "w", newline="", encoding="utf8") as f:
                f.write(r.time_heading + "," + ",".join(NAMES) + "\n")
                for k in range(0, r.count, BLOCK):
                    np.savetxt(f, r.data[k : k + BLOCK], delimiter=",", fmt="%.17g")
        elif kind == "wav":
            rate = int(float(q.get("rate") or r.wav_sample_rate()))
            if not 1 <= rate <= 0xFFFFFFFF // 12:
                raise ValueError("WAV sample rate outside PCM range")
            scale = float(q.get("scale", 1))
            if not np.isfinite(scale) or scale <= 0:
                raise ValueError("WAV full-scale voltage must be positive")
            size = r.count * 12
            with open(target, "wb") as w:
                if size > 0xFFFFFFFF - 36:
                    w.write(
                        b"RF64"
                        + struct.pack("<I", 0xFFFFFFFF)
                        + b"WAVEds64"
                        + struct.pack("<IQQQI", 28, size + 72, size, r.count, 0)
                    )
                else:
                    w.write(b"RIFF" + struct.pack("<I", size + 36) + b"WAVE")
                w.write(
                    b"fmt "
                    + struct.pack("<IHHIIHH", 16, 1, 6, rate, rate * 12, 12, 16)
                    + b"data"
                    + struct.pack("<I", min(size, 0xFFFFFFFF))
                )
                for k in range(0, r.count, BLOCK):
                    w.write(
                        (
                            np.where(r.logic[k : k + BLOCK, :6] > 0, 32767, -32768)
                            if q.get("wav_mode", "logic") == "logic"
                            else np.clip(r.data[k : k + BLOCK, 1:] / scale, -1, 1)
                            * 32767
                        )
                        .astype("<i2")
                        .tobytes()
                    )
        elif kind == "pdf":
            from report import create_report

            create_report(r, target, q.get("serial", ""))
        else:
            raise ValueError("Unknown export format")
        return target, kind

    def export(self, r, q):
        target, kind = self.prepare_export(r, q)
        return self.send_export(target, kind)

    def send_export(self, target, kind):
        try:
            self.send_response(200)
            self.send_header(
                "Content-Type",
                {"csv": "text/csv", "wav": "audio/wav", "pdf": "application/pdf"}[kind],
            )
            self.send_header(
                "Content-Disposition",
                f'attachment; filename="encoder-{"renamed" if "renamed-export-" in target.name else "analysis"}{"-refined-B" if target.name.startswith("refined-") else ""}.{kind}"',
            )
            self.send_header("Content-Length", str(target.stat().st_size))
            self.end_headers()
            with open(target, "rb") as f:
                shutil.copyfileobj(f, self.wfile, 4 * 1024 * 1024)
        finally:
            target.unlink(missing_ok=True)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--no-browser", action="store_true")
    args = p.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"Offline viewer: http://127.0.0.1:{args.port}", flush=True)
    if not args.no_browser:
        webbrowser.open(f"http://127.0.0.1:{args.port}")
    server.serve_forever()
