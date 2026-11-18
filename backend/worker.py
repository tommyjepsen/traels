# /// script
# requires-python = ">=3.11,<3.14"
# dependencies = [
#   "transformers>=5",
#   "sentence-transformers[image]",
#   "torch",
#   "onnxruntime",
#   "numpy",
#   "pillow",
#   "huggingface_hub",
#   "av",
#   "soundfile",
#   "scipy",
# ]
# ///
"""Traels inference worker. JSON-lines over stdin/stdout (see ARCHITECTURE.md). Logs go to stderr."""
import json
import os
import queue
import sys
import threading
import traceback

os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

_out_lock = threading.Lock()
_stdout = sys.stdout
sys.stdout = sys.stderr  # stray prints from libraries must never corrupt the protocol


def send(obj):
    line = json.dumps(obj, ensure_ascii=False)
    with _out_lock:
        _stdout.write(line + "\n")
        _stdout.flush()


def log(*a):
    print(*a, file=sys.stderr, flush=True)


jobs = queue.Queue()
cancel_events = {}  # id -> Event
cancel_lock = threading.Lock()


def run_job(req):
    import detectors
    from detectors import analysis
    rid, cmd = str(req.get("id", "")), req.get("cmd")
    with cancel_lock:
        ev = cancel_events.setdefault(rid, threading.Event())
    if ev.is_set():
        send({"id": rid, "type": "error", "message": "Cancelled"})
        return

    def progress(fraction, message):
        send({"id": rid, "type": "progress", "fraction": round(float(fraction), 4), "message": message})

    ctx = detectors.Context(ev, progress)
    try:
        if cmd == "analyze":
            result = analysis.analyze(req, ctx)
            send({"id": rid, "type": "result", "result": result})
        elif cmd == "fetch":
            from detectors import fetch
            send({"id": rid, "type": "fetched", "file": fetch.fetch(req, ctx)})
        elif cmd in ("download", "delete"):
            det = detectors.get(req.get("model", ""))
            if det is None:
                raise ValueError(f"Unknown model: {req.get('model')}")
            if cmd == "download":
                detectors.download(det, progress)
            else:
                detectors.delete(det)
            send({"id": rid, "type": "done"})
    except detectors.Cancelled:
        send({"id": rid, "type": "error", "message": "Cancelled"})
    except Exception as e:  # noqa: BLE001
        log(traceback.format_exc())
        send({"id": rid, "type": "error", "message": str(e) or e.__class__.__name__})
    finally:
        with cancel_lock:
            cancel_events.pop(rid, None)


def worker_loop():
    while True:
        req = jobs.get()
        if req is None:
            return
        run_job(req)


def handle(req):
    rid, cmd = str(req.get("id", "")), req.get("cmd")
    if cmd == "models":
        import detectors
        send({"id": rid, "type": "models", "models": [d.info() for d in detectors.all_detectors()]})
    elif cmd == "cancel":
        with cancel_lock:
            cancel_events.setdefault(str(req.get("target", "")), threading.Event()).set()
        send({"id": rid, "type": "done"})
    elif cmd in ("analyze", "fetch", "download", "delete"):
        with cancel_lock:
            cancel_events.setdefault(rid, threading.Event())
        jobs.put(req)
    else:
        send({"id": rid, "type": "error", "message": f"Unknown command: {cmd}"})


def main():
    # heavy imports first so "ready" means "ready"
    import numpy, PIL.Image, torch, onnxruntime, transformers, huggingface_hub, av  # noqa: F401,E401
    try:
        import sentence_transformers  # noqa: F401
    except Exception as e:  # noqa: BLE001
        log("sentence_transformers import failed:", e)
    import detectors
    detectors.all_detectors()
    threading.Thread(target=worker_loop, daemon=True).start()
    send({"id": "0", "type": "ready", "version": "1"})
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
            if not isinstance(req, dict):
                raise ValueError("request must be an object")
        except Exception as e:  # noqa: BLE001
            send({"id": "", "type": "error", "message": f"Bad request: {e}"})
            continue
        try:
            handle(req)
        except Exception as e:  # noqa: BLE001
            log(traceback.format_exc())
            send({"id": str(req.get("id", "")), "type": "error", "message": str(e)})
    jobs.put(None)


if __name__ == "__main__":
    main()
