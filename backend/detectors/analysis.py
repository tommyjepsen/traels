"""analyze: dispatch by kind -> AnalysisResult dict."""
from __future__ import annotations

import time
import traceback

from . import Cancelled, Context, get, log, to_score
from . import facts as factsmod
from . import video as videomod

KIND_MODELS = {  # request kind -> detector kinds applicable
    "image": {"image"},
    "audio": {"audio", "music"},
    "text": {"text"},
    "video": {"image", "video", "audio", "music"},
}
NOUN = {"image": "image", "video": "video", "audio": "audio clip", "text": "text"}
AI_VERB = {"image": "was made by AI", "video": "is AI-generated or fake", "audio": "was made by AI",
           "text": "was written by AI"}
HEADLINES = {
    "image": ("Likely a real photo", "Can't tell", "Likely AI-generated"),
    "video": ("Likely a real video", "Can't tell", "Likely AI-generated or fake"),
    "audio": ("Likely a real recording", "Can't tell", "Likely AI-generated audio"),
    "text": ("Likely written by a human", "Can't tell", "Likely AI-written"),
}
CAVEAT = "Detectors can be wrong — treat this as a hint, not proof."


GROUP = {"image": "picture", "video": "picture", "audio": "voice", "music": "music", "text": "text"}


def verdict_for(score: float) -> str:
    return "likely_real" if score < 35 else "likely_ai" if score > 65 else "uncertain"


def _run_single(dets, x, ctx, lo=0.05, hi=0.95, suffix=""):
    entries, errors = [], []
    n = len(dets)
    for i, d in enumerate(dets):
        ctx.progress(lo + (hi - lo) * i / n, f"Running {d.name} ({i + 1}/{n})")
        try:
            d.load()
            p, detail, extra = d.predict(x)
            entries.append({"id": d.id, "name": d.name, "score": to_score(p, d.threshold), "p": p,
                            "threshold": d.threshold, "detail": detail + suffix,
                            "inconclusive": bool(extra.get("inconclusive"))})
        except Cancelled:
            raise
        except Exception as e:  # noqa: BLE001
            log(traceback.format_exc())
            errors.append(f"{d.name}: {e}")
    return entries, errors


def analyze(req: dict, ctx: Context) -> dict:
    t0 = time.time()
    kind = req.get("kind")
    if kind not in KIND_MODELS:
        raise ValueError(f"Unknown kind: {kind}")
    path, text = req.get("path"), req.get("text")
    if kind == "text":
        if not (text or "").strip():
            raise ValueError("No text to analyse")
    elif not path:
        raise ValueError("Missing file path")

    wanted, skipped, notes = [], [], []
    for mid in req.get("models") or []:
        d = get(mid)
        if d is None:
            notes.append(f"Unknown model '{mid}' ignored.")
        elif d.kind not in KIND_MODELS[kind]:
            continue
        elif not d.installed():
            skipped.append(d.name)
        else:
            wanted.append(d)
    if skipped:
        notes.append(f"Skipped {', '.join(skipped)} (not downloaded).")
    if not wanted:
        raise RuntimeError("No installed detector is available for this " + NOUN[kind] + ". Turn one on in Settings.")

    ctx.progress(0.0, "Starting")
    facts = factsmod.facts_for(kind, path, text)
    source = req.get("source") or {}
    if source.get("host"):
        facts.insert(0, {"label": "Source", "value": source["host"]})
    if source.get("platform") and kind in ("image", "video"):
        notes.append(f"It came from {source['host']}, which re-compresses uploads, so fakes often score lower here.")
    ctx.check()

    errors, extra_facts = [], []
    if kind == "image":
        from .image import load_rgb
        entries, errors = _run_single(wanted, load_rgb(path), ctx)
    elif kind == "text":
        entries, errors = _run_single(wanted, text, ctx)
    elif kind == "audio":
        from .audio import decode_audio
        ctx.progress(0.02, "Decoding audio")
        audio = decode_audio(path)
        if audio is None or audio.size == 0:
            raise RuntimeError("This file has no audio to analyse.")
        entries, errors = _run_single(wanted, audio, ctx)
    else:
        entries, errors, extra_facts = videomod.run(
            [d for d in wanted if d.kind == "image"], [d for d in wanted if d.kind == "video"],
            [d for d in wanted if d.kind in ("audio", "music")], path, ctx)
    ctx.check()

    if not entries:
        raise RuntimeError("All detectors failed. " + " ".join(errors))

    ctx.progress(0.97, "Summarising")
    # Average within each signal (picture / voice / music), then take the most suspicious signal:
    # a cloned voice over real footage, or a fake picture with a real soundtrack, is still fake.
    # Detectors that couldn't judge (voice models on audio with background sound) don't count either way.
    counted = [e for e in entries if not e.get("inconclusive", False)]
    if any(e.get("inconclusive", False) for e in entries):
        notes.append("There is music or background sound under the voice, which hides cloned voices from "
                     "the voice detectors, so the voice can't be judged.")
    groups: dict[str, list[float]] = {}
    for e in counted:
        groups.setdefault(GROUP[get(e["id"]).kind], []).append(e["score"])
    means = {g: sum(s) / len(s) for g, s in groups.items()}
    if means:
        top = max(means, key=means.get)
        combined = means[top]
        if len(means) > 1:
            notes.append(f"The score follows the {top} detectors, the most suspicious signal here.")
        ai_votes = sum(e["p"] >= e["threshold"] for e in counted)
        n = len(counted)
        if n == 1:
            who = "The detector thinks" if ai_votes else "The detector doesn't think"
            summary = f"{who} this {NOUN[kind]} {AI_VERB[kind]}."
        else:
            summary = f"{ai_votes} of {n} detectors think this {NOUN[kind]} {AI_VERB[kind]}."
    else:
        combined = 50.0
        summary = f"None of the detectors could judge this {NOUN[kind]}."
    summary_parts = [summary] + notes + [f"Error in {e}." for e in errors]  # the app shows CAVEAT itself

    res = next((f["value"] for f in facts if f["label"] == "Resolution"), "")
    if "picture" in means and "×" in res and min(int(x) for x in res.split("×")) < 512:
        summary_parts.insert(1, "It is low resolution, which hides the traces detectors look for, so fakes often score low.")
    if kind == "text" and len(text.split()) < 50:
        summary_parts.insert(1, "This text is short (under 50 words), so detectors are especially unreliable here.")

    v = verdict_for(combined)
    headline = HEADLINES[kind][{"likely_real": 0, "uncertain": 1, "likely_ai": 2}[v]]
    return {
        "kind": kind,
        "score": round(combined, 1),
        "verdict": v,
        "headline": headline,
        "summary": " ".join(summary_parts),
        "models": [{"id": e["id"], "name": e["name"], "score": round(e["score"], 1),
                    "verdict": "inconclusive" if e.get("inconclusive", False) else verdict_for(e["score"]),
                    "inconclusive": e.get("inconclusive", False), "detail": e["detail"]} for e in entries],
        "facts": facts + extra_facts,
        "seconds": round(time.time() - t0, 2),
    }
