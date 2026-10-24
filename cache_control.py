"""Coordinate explicit cache removal with analysis, readers and downloads."""

from contextlib import contextmanager
from pathlib import Path
import os
import shutil
import threading
import time
import uuid
import numpy as np


def linked(path):
    return path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction())


class CacheManager:
    def __init__(self, root):
        self.root = Path(root).absolute()
        self.lock = threading.RLock()
        self.stats_lock = threading.Lock()
        self.users = self.workers = 0
        self.clearing = False
        self.revision = uuid.uuid4().hex
        self.last_scan = -float("inf")
        self.size = self.files = 0

    @contextmanager
    def use(self):
        with self.lock:
            if self.clearing:
                raise ValueError("Cache deletion is in progress. Please wait.")
            self.users += 1
        try:
            yield
        finally:
            with self.lock:
                self.users -= 1

    def start_worker(self, function):
        with self.lock:
            if self.clearing:
                raise ValueError("Cache deletion is in progress. Please wait.")
            self.workers += 1

        def run():
            try:
                function()
            finally:
                with self.lock:
                    self.workers -= 1

        try:
            threading.Thread(target=run, daemon=True).start()
        except Exception:
            with self.lock:
                self.workers -= 1
            raise

    def validate_root(self):
        if (
            self.root.name != ".cache"
            or linked(self.root)
            or self.root.resolve() != self.root
        ):
            raise ValueError(
                "Refusing cache deletion: the configured .cache folder is redirected or invalid"
            )

    def scan(self, force=False):
        with self.stats_lock:
            if not force and time.monotonic() - self.last_scan < 2:
                return self.size, self.files
            size = files = 0
            stack = [self.root] if self.root.exists() and not linked(self.root) else []
            while stack:
                directory = stack.pop()
                try:
                    with os.scandir(directory) as entries:
                        for entry in entries:
                            try:
                                path = Path(entry.path)
                                if linked(path):
                                    continue
                                if entry.is_dir(follow_symlinks=False):
                                    stack.append(path)
                                elif entry.is_file(follow_symlinks=False):
                                    size += entry.stat(follow_symlinks=False).st_size
                                    files += 1
                            except (OSError, ValueError):
                                continue
                except OSError:
                    continue
            self.size, self.files, self.last_scan = size, files, time.monotonic()
            return size, files

    def info(self, force=False):
        size, files = self.scan(force)
        with self.lock:
            return dict(
                bytes=size,
                files=files,
                busy=self.users + self.workers,
                clearing=self.clearing,
                revision=self.revision,
            )

    @staticmethod
    def close_recordings(recordings):
        seen = set()
        maps = {}
        errors = []

        def visit(value):
            if id(value) in seen:
                return
            seen.add(id(value))
            if isinstance(value, np.memmap):
                mmap = getattr(value, "_mmap", None)
                if mmap is not None:
                    maps[id(mmap)] = mmap
            elif isinstance(value, np.ndarray):
                visit(value.base)
            elif isinstance(value, dict):
                for item in value.values():
                    visit(item)
            elif isinstance(value, (tuple, list)):
                for item in value:
                    visit(item)

        for recording in recordings:
            visit(vars(recording))
            db = getattr(recording, "faultdb", None)
            if db is not None:
                try:
                    db.close()
                    recording.faultdb = None
                except Exception as exc:
                    errors.append(str(exc))
        for mmap in maps.values():
            if not mmap.closed:
                try:
                    mmap.close()
                except Exception as exc:
                    errors.append(str(exc))
        return errors

    def clear(self, jobs, exports):
        with self.lock:
            self.validate_root()
            if (
                self.clearing
                or self.users
                or self.workers
                or any(not r.ready and not r.error for r in jobs.values())
            ):
                raise ValueError(
                    "Cache is in use. Finish or cancel analysis and wait for exports/downloads to complete."
                )
            self.clearing = True
        errors = []
        before = self.size
        try:
            before, _ = self.scan(force=True)
            # No new consumers may enter until handles are closed and deletion ends.
            with self.lock:
                recordings = list(jobs.values())
                jobs.clear()
                exports.clear()
                self.revision = uuid.uuid4().hex
            try:
                errors.extend(
                    dict(name="recording handles", error=error)
                    for error in self.close_recordings(recordings)
                )
            except Exception as exc:
                errors.append(dict(name="recording handles", error=str(exc)))
            if self.root.exists():
                for path in self.root.iterdir():
                    try:
                        if path.parent != self.root:
                            raise ValueError("Unexpected cache child location")
                        if path.is_symlink():
                            path.unlink()
                        elif hasattr(path, "is_junction") and path.is_junction():
                            # Remove the directory link itself, never its target.
                            path.rmdir()
                        elif path.resolve().is_relative_to(self.root.resolve()):
                            if path.is_dir():
                                shutil.rmtree(path)
                            else:
                                path.unlink()
                        else:
                            raise ValueError("Resolved cache entry is outside .cache")
                    except (OSError, ValueError) as exc:
                        errors.append(dict(name=path.name, error=str(exc)))
        finally:
            with self.lock:
                self.clearing = False
        state = self.info(force=True)
        return dict(
            state,
            complete=not errors,
            errors=errors,
            deleted_bytes=max(0, before - state["bytes"]),
        )
