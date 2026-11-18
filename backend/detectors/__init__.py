"""Detector registry + shared base class.

Each detector is a class (see image.py / text.py / audio.py / music.py). Instances are
singletons held in REGISTRY; models are loaded lazily on first predict().
"""
from __future__ import annotations

import os
import sys
import threading
import time


def log(*a):
    print(*a, file=sys.stderr, flush=True)


class Cancelled(Exception):
    pass


class Context:
    """Passed through an analysis: cooperative cancellation + progress reporting."""

    def __init__(self, cancel_event=None, progress=None):
        self.cancel_event = cancel_event or threading.Event()
        self._progress = progress

    def check(self):
        if self.cancel_event.is_set():
            raise Cancelled()

    def progress(self, fraction, message):
        self.check()
        if self._progress:
            self._progress(max(0.0, min(1.0, fraction)), message)


def to_score(p: float, t: float) -> float:
    """Map probability p to a 0-100 score with the model's threshold t sitting at 50."""
    p = max(0.0, min(1.0, float(p)))
    t = min(max(float(t), 1e-6), 1 - 1e-6)
    s = 50 * p / t if p < t else 50 + 50 * (p - t) / (1 - t)
    return max(0.0, min(100.0, s))


def pick_device() -> str:
    try:
        import torch
        if torch.cuda.is_available():
            return "cuda"
        if torch.backends.mps.is_available():
            return "mps"
    except Exception:
        pass
    return "cpu"


class Detector:
    id = ""
    name = ""
    kind = "image"  # image | video | audio | music | text
    repo = ""
    license = ""
    size_mb = 0
    description = ""
    recommended = False
    threshold = 0.5
    files: list[str] | None = None  # None = full snapshot
    extra_files: list[tuple[str, list[str]]] = []  # [(other_repo, [files])] also needed (e.g. a feature extractor)

    def __init__(self):
        self._loaded = False
        self._lock = threading.Lock()

    # ---- cache state -------------------------------------------------
    def installed(self) -> bool:
        try:
            from huggingface_hub import try_to_load_from_cache, snapshot_download
            for repo, files in [(self.repo, self.files)] + list(self.extra_files):
                if files:
                    for f in files:
                        if not isinstance(try_to_load_from_cache(repo, f), str):
                            return False
                else:
                    snapshot_download(repo, local_files_only=True)
            return True
        except Exception:
            return False

    def info(self) -> dict:
        return {"id": self.id, "name": self.name, "kind": self.kind, "repo": self.repo,
                "license": self.license, "size_mb": self.size_mb, "installed": self.installed(),
                "description": self.description, "recommended": self.recommended}

    # ---- model lifecycle ---------------------------------------------
    def _load(self):
        raise NotImplementedError

    def load(self):
        with self._lock:
            if not self._loaded:
                t = time.time()
                log(f"loading {self.id} ...")
                self._load()
                self._loaded = True
                log(f"loaded {self.id} in {time.time() - t:.1f}s")

    def unload(self):
        with self._lock:
            self._loaded = False
            for k in [k for k in self.__dict__ if k not in ("_loaded", "_lock")]:
                del self.__dict__[k]

    def predict(self, x):
        """-> (p in [0,1] probability of AI/fake, detail string, extra dict)"""
        raise NotImplementedError


# ---- registry --------------------------------------------------------
REGISTRY: dict[str, Detector] = {}


def register(cls):
    inst = cls()
    REGISTRY[inst.id] = inst
    return cls


def _load_modules():
    from . import image, text, audio, music  # noqa: F401  (registration side effects)


def all_detectors() -> list[Detector]:
    if not REGISTRY:
        _load_modules()
    return list(REGISTRY.values())


def get(model_id: str) -> Detector | None:
    all_detectors()
    return REGISTRY.get(model_id)


# ---- download / delete ----------------------------------------------
def _repo_dir(repo: str) -> str:
    from huggingface_hub import constants
    return os.path.join(constants.HF_HUB_CACHE, "models--" + repo.replace("/", "--"))


def _dir_bytes(path: str) -> int:
    total = 0
    for root, _dirs, files in os.walk(os.path.join(path, "blobs")):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


def download(det: Detector, progress) -> None:
    """Download the model's files. progress(fraction, message). Network is enabled only here."""
    from huggingface_hub import snapshot_download, constants

    old_env = os.environ.get("HF_HUB_OFFLINE")
    old_const = constants.HF_HUB_OFFLINE
    os.environ["HF_HUB_OFFLINE"] = "0"
    constants.HF_HUB_OFFLINE = False
    result = {}

    def run():
        try:
            snapshot_download(det.repo, allow_patterns=det.files)
            for repo, files in det.extra_files:
                snapshot_download(repo, allow_patterns=files)
        except BaseException as e:  # noqa: BLE001
            result["err"] = e

    try:
        base = _dir_bytes(_repo_dir(det.repo))
        t = threading.Thread(target=run, daemon=True)
        t.start()
        total = max(det.size_mb, 1) * 1024 * 1024
        progress(0.0, f"Downloading {det.name}")
        while t.is_alive():
            t.join(0.5)
            done = max(0, _dir_bytes(_repo_dir(det.repo)) - base)
            progress(min(0.99, done / total), f"Downloading {det.name} ({done // (1024 * 1024)} MB)")
        if "err" in result:
            raise result["err"]
    finally:
        if old_env is None:
            os.environ.pop("HF_HUB_OFFLINE", None)
        else:
            os.environ["HF_HUB_OFFLINE"] = old_env
        constants.HF_HUB_OFFLINE = old_const
    progress(1.0, "Done")


def delete(det: Detector) -> None:
    from huggingface_hub import scan_cache_dir
    det.unload()
    info = scan_cache_dir()
    for repo in info.repos:
        if repo.repo_id == det.repo and repo.repo_type == "model":
            strategy = info.delete_revisions(*[r.commit_hash for r in repo.revisions])
            strategy.execute()
            log(f"deleted {det.repo}, freed {strategy.expected_freed_size_str}")
