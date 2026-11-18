"""Cheap, non-ML facts per kind."""
from __future__ import annotations

import os
import re

from . import log

AI_MARKERS = [b"trainedAlgorithmicMedia", b"compositeWithTrainedAlgorithmicMedia", b"algorithmicMedia"]
C2PA_MARKERS = [b"c2pa", b"jumb", b"contentcredentials", b"ContentCredentials"]
MADE_WITH_AI = re.compile(rb"Made with[^\x00\n]{0,24}?\bAI\b|Made with (?:Google AI|Midjourney|DALL)", re.I)


def fact(label, value):
    return {"label": label, "value": str(value)}


def fmt_duration(sec: float) -> str:
    sec = max(0.0, sec)
    m, s = divmod(sec, 60)
    return f"{int(m)}:{s:04.1f}" if m else f"{s:.1f} s"


def content_credentials(path: str) -> dict:
    try:
        with open(path, "rb") as f:
            data = f.read(64 * 1024 * 1024)
    except OSError:
        return fact("Content credentials", "unreadable")
    ai = [m for m in AI_MARKERS if m in data] or ([1] if MADE_WITH_AI.search(data) else [])
    c2pa = [m for m in C2PA_MARKERS if m in data]
    if ai:
        return fact("Content credentials", "found (says AI)")
    if c2pa:
        return fact("Content credentials", "found")
    return fact("Content credentials", "none found")


def image_facts(path: str) -> list[dict]:
    from PIL import Image
    out = []
    with Image.open(path) as im:
        out.append(fact("Resolution", f"{im.width}×{im.height}"))
        out.append(fact("Format", im.format or os.path.splitext(path)[1].lstrip(".").upper() or "unknown"))
        try:
            ex = im.getexif()
            make, model = str(ex.get(271, "")).strip(), str(ex.get(272, "")).strip()
        except Exception:
            make = model = ""
        cam = " ".join(x for x in (make, model) if x)
        out.append(fact("EXIF camera", cam or "none"))
    out.append(content_credentials(path))
    return out


def av_facts(path: str, kind: str) -> list[dict]:
    import av
    out = []
    with av.open(path) as c:
        dur = (c.duration / 1_000_000) if c.duration else None
        if dur:
            out.append(fact("Duration", fmt_duration(dur)))
        if c.streams.video:
            v = c.streams.video[0]
            out.append(fact("Resolution", f"{v.codec_context.width}×{v.codec_context.height}"))
            if v.average_rate:
                out.append(fact("Frame rate", f"{float(v.average_rate):.1f} fps"))
        if c.streams.audio:
            a = c.streams.audio[0]
            out.append(fact("Sample rate", f"{a.codec_context.sample_rate} Hz"))
            out.append(fact("Channels", a.codec_context.channels if hasattr(a.codec_context, "channels") else "?"))
        elif kind == "video":
            out.append(fact("Audio track", "none"))
    out.append(content_credentials(path))
    return out


def text_facts(text: str) -> list[dict]:
    return [fact("Words", len(text.split())), fact("Characters", len(text))]


def facts_for(kind: str, path: str | None = None, text: str | None = None) -> list[dict]:
    try:
        if kind == "image":
            return image_facts(path)
        if kind in ("audio", "video"):
            return av_facts(path, kind)
        if kind == "text":
            return text_facts(text or "")
    except Exception as e:  # noqa: BLE001
        log("facts failed:", e)
    return []
