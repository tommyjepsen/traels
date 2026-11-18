"""Video pipeline: sample frames -> image models per frame; audio track -> audio/music models."""
from __future__ import annotations

import traceback

from PIL import Image

from . import Cancelled, log, to_score
from . import audio as audiomod

N_FRAMES = 12


def sample_frames(path: str, n: int = N_FRAMES) -> tuple[list[Image.Image], float | None]:
    """~n evenly spaced RGB frames. Returns (frames, duration_seconds)."""
    import av
    frames: list[Image.Image] = []
    with av.open(path) as c:
        if not c.streams.video:
            return [], None
        stream = c.streams.video[0]
        dur = (c.duration / 1_000_000) if c.duration else None
        if dur and dur > 0:
            times = [dur * (i + 0.5) / n for i in range(n)]
            for t in times:
                try:
                    c.seek(int(t * 1_000_000), any_frame=False, backward=True)
                    got = None
                    for fr in c.decode(stream):
                        got = fr
                        if fr.time is None or fr.time >= t - 1e-3:
                            break
                    if got is not None:
                        frames.append(got.to_image().convert("RGB"))
                except Exception as e:  # noqa: BLE001
                    log("seek failed:", e)
        if len(frames) < min(n, 2):  # unknown duration or seeking failed: decode sequentially
            frames = []
            c.seek(0)
            allf = [f.to_image().convert("RGB") for f in c.decode(stream)]
            if allf:
                step = max(1, len(allf) / n)
                frames = [allf[int(i * step)] for i in range(min(n, len(allf)))]
    return frames, None


def run(img_dets, video_dets, audio_dets, path, ctx, lo=0.05, hi=0.95):
    """-> (entries, errors, extra_facts). entries are dicts with id/name/score/p/threshold/detail."""
    entries, errors, facts = [], [], []
    ctx.progress(lo, "Reading video frames")
    frames, _ = sample_frames(path)
    facts.append({"label": "Frames sampled", "value": str(len(frames))})
    ctx.check()

    audio = None
    if audio_dets:
        ctx.progress(lo, "Reading audio track")
        try:
            audio = audiomod.decode_audio(path)
        except Exception as e:  # noqa: BLE001
            log("audio decode failed:", e)
        if audio is None or audio.size < 16000 * 0.5:
            audio = None
            facts.append({"label": "Audio analysis", "value": "skipped (no usable audio track)"})

    steps = len(frames) * len(img_dets) + len(video_dets) + (len(audio_dets) if audio is not None else 0)
    done = 0

    def tick(msg):
        nonlocal done
        ctx.progress(lo + (hi - lo) * done / max(steps, 1), msg)

    for d in img_dets:
        ps = []
        try:
            d.load()
            for i, fr in enumerate(frames):
                tick(f"Running {d.name} on frame {i + 1}/{len(frames)}")
                p, _detail, _x = d.predict(fr)
                ps.append(p)
                done += 1
        except Cancelled:
            raise
        except Exception as e:  # noqa: BLE001
            log(traceback.format_exc())
            errors.append(f"{d.name}: {e}")
            continue
        if not ps:
            errors.append(f"{d.name}: no frames")
            continue
        scores = [to_score(p, d.threshold) for p in ps]
        entries.append({"id": d.id, "name": d.name, "score": sum(scores) / len(scores),
                        "p": sum(ps) / len(ps), "threshold": d.threshold,
                        "detail": f"Mean of {len(ps)} frames (max {max(scores):.0f}); threshold {d.threshold:g}"})

    for d in video_dets:
        tick(f"Running {d.name}")
        try:
            d.load()
            p, detail, _x = d.predict(frames)
            entries.append({"id": d.id, "name": d.name, "score": to_score(p, d.threshold), "p": p,
                            "threshold": d.threshold, "detail": detail})
        except Cancelled:
            raise
        except Exception as e:  # noqa: BLE001
            log(traceback.format_exc())
            errors.append(f"{d.name}: {e}")
        done += 1

    if audio is not None:
        for d in audio_dets:
            tick(f"Running {d.name} on audio")
            try:
                d.load()
                p, detail, x = d.predict(audio)
                entries.append({"id": d.id, "name": d.name, "score": to_score(p, d.threshold), "p": p,
                                "threshold": d.threshold, "detail": detail + " (audio track)",
                                "inconclusive": bool(x.get("inconclusive"))})
            except Cancelled:
                raise
            except Exception as e:  # noqa: BLE001
                log(traceback.format_exc())
                errors.append(f"{d.name}: {e}")
            done += 1
    return entries, errors, facts
