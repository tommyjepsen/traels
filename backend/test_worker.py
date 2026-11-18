#!/usr/bin/env python3
"""End-to-end harness for worker.py (stdlib only).

    python3 backend/test_worker.py            # everything
    python3 backend/test_worker.py image video  # only some stages: models image video download text audio music

The image and video stages need two sample images, 1_fake.jpg (AI-generated) and 1_original.jpg (a real
photo), in the folder named by TRAELS_SAMPLES; they are skipped when it is not set.
"""
import json
import os
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SAMPLES = os.environ.get("TRAELS_SAMPLES")
TMP = tempfile.mkdtemp(prefix="traels-test-")
DOWNLOAD = ["fakespot", "voice_wav2vec2", "music_ast", "desklib"]

AI_TEXT = ("Artificial intelligence has become an integral part of modern society, offering numerous benefits across "
           "various sectors. Furthermore, it is important to note that the rapid advancement of technology presents both "
           "opportunities and challenges. In conclusion, by leveraging these innovations responsibly, organizations can "
           "foster sustainable growth, enhance efficiency, and ultimately deliver value to stakeholders worldwide.")
HUMAN_TEXT = ("ok so yesterday I tried to fix my bike chain and totally made it worse lol. grease everywhere, my "
              "neighbour Jorge laughed at me from his balcony for like ten minutes. ended up walking to work in the rain "
              "with one shoe untied. anyway the coffee at that new place on Vesterbro is actually decent, we should go.")

proc = subprocess.Popen(["uv", "run", "--script", os.path.join(HERE, "worker.py")], stdin=subprocess.PIPE,
                        stdout=subprocess.PIPE, text=True, bufsize=1,
                        env={**os.environ, "HF_HUB_OFFLINE": "1", "PYTHONUNBUFFERED": "1", "TOKENIZERS_PARALLELISM": "false"})
events = queue.Queue()
_next = [0]


def reader():
    for line in proc.stdout:
        try:
            events.put(json.loads(line))
        except ValueError:
            print("!! non-JSON on stdout:", line.rstrip())
    events.put(None)


threading.Thread(target=reader, daemon=True).start()


def send(cmd, **kw):
    _next[0] += 1
    rid = str(_next[0])
    proc.stdin.write(json.dumps({"id": rid, "cmd": cmd, **kw}) + "\n")
    proc.stdin.flush()
    return rid


def wait(rid, timeout=1800, quiet=False):
    """Print events for rid until a terminal one; return it."""
    end, last = time.time() + timeout, -1
    while time.time() < end:
        try:
            ev = events.get(timeout=5)
        except queue.Empty:
            continue
        if ev is None:
            raise SystemExit("worker exited")
        if ev.get("id") != rid:
            continue
        if ev["type"] == "progress":
            if not quiet and (ev["fraction"] - last >= 0.05 or ev["fraction"] >= 1):
                print(f"   .. {ev['fraction']:.2f} {ev['message']}")
                last = ev["fraction"]
            continue
        return ev
    raise SystemExit("timeout")


def show(ev):
    if ev["type"] == "result":
        r = ev["result"]
        print(f"   => {r['kind']} score={r['score']} verdict={r['verdict']} '{r['headline']}' ({r['seconds']}s)")
        for m in r["models"]:
            print(f"      - {m['id']:<16} {m['score']:>5}  {m['verdict']:<11} {m['detail']}")
        print("      facts:", "; ".join(f"{f['label']}={f['value']}" for f in r["facts"]))
        print("      summary:", r["summary"])
    else:
        print("   =>", json.dumps(ev)[:600])


def analyze(title, **kw):
    print(f"\n## {title}")
    show(wait(send("analyze", **kw)))


def tts_file():
    out = os.path.join(TMP, "voice.wav")
    if shutil.which("say"):
        aiff = os.path.join(TMP, "voice.aiff")
        subprocess.run(["say", "-o", aiff, "Hello, this is a short test of a synthetic voice. The quick brown fox jumps "
                        "over the lazy dog, and then it goes home to rest after a long day."], check=True)
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", aiff, "-ar", "22050", out], check=True)
    return out


def make_video():
    out = os.path.join(TMP, "test.mp4")
    f = lambda n: os.path.join(SAMPLES, n)
    crop = "scale=640:360:force_original_aspect_ratio=increase,crop=640:360,setsar=1"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-loop", "1", "-t", "1.5", "-i", f("1_fake.jpg"),
                    "-loop", "1", "-t", "1.5", "-i", f("1_original.jpg"), "-f", "lavfi", "-t", "3",
                    "-i", "sine=frequency=440:sample_rate=44100",
                    "-filter_complex", f"[0:v]{crop}[a];[1:v]{crop}[b];[a][b]concat=n=2:v=1[v]",
                    "-map", "[v]", "-map", "2:a", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", "24",
                    "-c:a", "aac", "-shortest", out], check=True)
    return out


def main():
    stages = set(sys.argv[1:]) or {"models", "image", "video", "download", "text", "audio", "music"}
    t0 = time.time()
    ev = wait("0", timeout=1800)
    print(f"ready: {ev} after {time.time() - t0:.0f}s")

    mid = send("models")
    ev = wait(mid)
    models = ev["models"]
    print("\n## models")
    for m in models:
        print(f"   {m['id']:<16} {m['kind']:<6} installed={m['installed']!s:<5} rec={m['recommended']!s:<5} {m['size_mb']:>5}MB {m['license']}")
    ids = lambda kind: [m["id"] for m in models if m["kind"] == kind]

    if not SAMPLES and stages & {"image", "video"}:
        print("\n## image/video skipped: set TRAELS_SAMPLES to a folder with 1_fake.jpg and 1_original.jpg")
        stages -= {"image", "video"}
    if "image" in stages:
        for n in ("1_fake.jpg", "1_original.jpg"):
            analyze(f"image {n}", kind="image", path=os.path.join(SAMPLES, n), models=ids("image"))
    if "video" in stages:
        analyze("video (frames + audio track)", kind="video", path=make_video(), models=ids("image") + ids("audio") + ids("music"))
    if "download" in stages:
        for mid_ in DOWNLOAD:
            info = next(m for m in models if m["id"] == mid_)
            print(f"\n## download {mid_} (installed={info['installed']})")
            if not info["installed"]:
                show(wait(send("download", model=mid_)))
        print("   installed now:", [m["id"] for m in wait(send("models"))["models"] if m["installed"]])
    if "text" in stages:
        analyze("text (AI-sounding)", kind="text", text=AI_TEXT, models=["fakespot", "desklib"])
        analyze("text (casual human)", kind="text", text=HUMAN_TEXT, models=["fakespot", "desklib"])
        analyze("text (very short)", kind="text", text="This is a short sentence.", models=["fakespot", "desklib"])
    if "audio" in stages:
        analyze("audio (macOS TTS)", kind="audio", path=tts_file(), models=ids("audio") + ["music_ast"])
    if "music" in stages:
        analyze("music model on TTS (sanity)", kind="audio", path=tts_file(), models=["music_ast"])

    # cancel + error handling
    if "models" in stages:
        print("\n## error handling")
        show(wait(send("analyze", kind="image", path="/nonexistent.png", models=["smaug"])))
    proc.stdin.close()
    shutil.rmtree(TMP, ignore_errors=True)
    print(f"\nall done in {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
