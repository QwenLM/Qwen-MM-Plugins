"""Storage backend: local filesystem.

A memory library is a namespace laid out as:
    <root>/<ns>/store.json   (full MemoryStore.to_dict, incl. vectors)
    <root>/<ns>/meta.json    (light preview for listing / ns_info)
    <root>/<ns>/clips/win_NNN.mp4   (original-resolution clips for replay)

Interface used by pipeline: available, status_line, save, save_async, load,
list_namespaces, meta, delete, clip_key, upload_clip_async, download_by_key.

Memories are stored on local disk, next to the video by default.
"""

import json
import os
import shutil
import time
from concurrent.futures import ThreadPoolExecutor

import env_config as config
from store_writer import MemoryStore


class _LocalBackend:
    """Memory libraries on local disk, rooted at config.local_dir()."""

    def __init__(self, root):
        self.root = root
        self._exec = None

    def _ex(self):
        if self._exec is None:
            self._exec = ThreadPoolExecutor(max_workers=1, thread_name_prefix="local-save")
        return self._exec

    def _ns_dir(self, ns):
        """Namespace must stay a single directory name — a namespace like "../../x" would otherwise
        write outside the library root. The service layer validates too, but the build path does not
        come through it."""
        name = str(ns).strip().strip("/")
        if not name or name in {".", ".."} or "/" in name or "\\" in name:
            raise ValueError(f"invalid namespace: {ns!r}")
        return os.path.join(self.root, name)

    def available(self):
        return True

    def status_line(self):
        return f"💾 本机存储：{self.root}"

    def _meta_dict(self, ns, sd, extra=None):
        m = {
            "namespace": ns,
            "updated": time.strftime("%Y-%m-%d %H:%M:%S"),
            "n_clips": len(sd.get("episodic", [])),
            "n_entities": len(sd.get("entities", [])),
            "n_semantic": len(sd.get("semantic", [])),
            "dense_ok": bool(sd.get("dense_ok")),
            "processed_sec": sd.get("processed_sec")
            or float(max((e.get("win_end", 0) or 0 for e in (sd.get("episodic") or [])), default=0)),
            "scene": (sd.get("global_summary") or "")[:120],
        }
        if extra:
            m.update(extra)
        return m

    @staticmethod
    def _dump_atomic(path, obj):
        """Write JSON via a temp file + rename, which is atomic within a directory.

        A build snapshots after every clip and runs for tens of minutes, so a Ctrl-C, kill or OOM
        lands in the middle of a write more often than one would think. Writing in place would leave
        a truncated store.json that no longer loads — taking resume down with it.
        """
        tmp = f"{path}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)

    def _write(self, ns, sd, meta):
        d = self._ns_dir(ns)
        os.makedirs(os.path.join(d, "clips"), exist_ok=True)
        self._dump_atomic(os.path.join(d, "store.json"), sd)
        self._dump_atomic(os.path.join(d, "meta.json"), self._meta_dict(ns, sd, meta))
        return d

    def save_async(self, ns, store, meta=None):
        snap = store.snapshot_for_async()  # cheap race-safe snapshot on the caller thread
        return self._ex().submit(self._write, ns, snap, meta)

    def load(self, ns):
        with open(os.path.join(self._ns_dir(ns), "store.json"), encoding="utf-8") as f:
            return MemoryStore.from_dict(json.load(f))

    def list_namespaces(self):
        if not os.path.isdir(self.root):
            return []
        return sorted(n for n in os.listdir(self.root) if os.path.exists(os.path.join(self.root, n, "store.json")))

    def clip_key(self, ns, idx):
        return os.path.join(self._ns_dir(ns), "clips", f"win_{int(idx):03d}.mp4")

    def upload_clip_async(self, ns, idx, path):
        dst = self.clip_key(ns, idx)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        return self._ex().submit(shutil.copyfile, path, dst)

    def download_by_key(self, key):
        with open(key, "rb") as f:
            return f.read()


_LOCAL = None


def get_backend():
    """Return the local-filesystem backend, rebuilt when config.local_dir() changes."""
    global _LOCAL
    if _LOCAL is None or _LOCAL.root != config.local_dir():
        _LOCAL = _LocalBackend(config.local_dir())
    return _LOCAL
