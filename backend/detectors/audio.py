"""Audio detectors (speech / voice-clone / deepfake) + shared audio decoding helpers.

New voice detectors: subclass _VoiceBase (or hf.HFPipelineDetector with
task="audio-classification", kind="audio") and decorate with @register.
"""
from __future__ import annotations

import math

import numpy as np

from . import register
from .hf import HFPipelineDetector

SAMPLE_RATE = 16000


def decode_audio(path: str, sr: int = SAMPLE_RATE, max_seconds: float | None = None) -> np.ndarray | None:
    """Decode the first audio stream of any container PyAV can read -> mono float32 numpy at `sr`.
    Returns None if the file has no audio stream."""
    import av
    with av.open(path) as container:
        if not container.streams.audio:
            return None
        stream = container.streams.audio[0]
        resampler = av.AudioResampler(format="flt", layout="mono", rate=sr)
        out, n = [], 0
        limit = int(max_seconds * sr) if max_seconds else None

        def take(frames):
            nonlocal n
            if frames is None:
                return
            if not isinstance(frames, (list, tuple)):
                frames = [frames]
            for f in frames:
                a = f.to_ndarray().reshape(-1)
                out.append(a.astype(np.float32, copy=False))
                n += a.shape[0]

        for frame in container.decode(stream):
            take(resampler.resample(frame))
            if limit and n >= limit:
                break
        take(resampler.resample(None))
    if not out:
        return np.zeros(0, dtype=np.float32)
    x = np.concatenate(out)
    return x[:limit] if limit else x


def resample(x: np.ndarray, sr_from: int, sr_to: int) -> np.ndarray:
    if sr_from == sr_to or x.size == 0:
        return x
    from scipy.signal import resample_poly
    g = math.gcd(int(sr_from), int(sr_to))
    return resample_poly(x, sr_to // g, sr_from // g).astype(np.float32)


def chunk_audio(x: np.ndarray, sr: int, seconds: float, max_chunks: int = 24, min_seconds: float = 1.0):
    """Split into non-overlapping windows -> [(chunk, weight_seconds)]. Evenly subsamples if too many;
    a short trailing remainder (< min_seconds) is dropped unless it is the only chunk."""
    size = max(1, int(seconds * sr))
    parts = [x[i:i + size] for i in range(0, len(x), size)]
    if len(parts) > 1 and len(parts[-1]) < min_seconds * sr:
        parts.pop()
    if len(parts) > max_chunks:
        idx = np.linspace(0, len(parts) - 1, max_chunks).round().astype(int)
        parts = [parts[i] for i in idx]
    return [(p, len(p) / sr) for p in parts if len(p)]


def noise_floor_db(x: np.ndarray, sr: int = SAMPLE_RATE) -> float:
    """Level of the quietest moments (5th percentile of 25 ms frames) relative to the speech (95th), in dB.
    Clean studio speech sits around -65 dB; a music bed or room noise lifts it towards 0."""
    f = int(0.025 * sr)
    n = len(x) // f
    if n < 10:
        return -100.0
    db = 20 * np.log10(np.sqrt(np.mean(x[:n * f].reshape(n, f) ** 2, axis=1)) + 1e-7)
    return float(np.percentile(db, 5) - np.percentile(db, 95))


class _VoiceBase(HFPipelineDetector):
    kind = "audio"
    task = "audio-classification"
    ai_labels = {"fake"}
    chunk_seconds = 10.0
    min_chunk_seconds = 2.0
    max_chunks = 12
    sample_rate = 16000
    # Voice-clone detectors are trained on clean speech: with music or noise underneath (floor above
    # this) they answer "real" for almost anything, so a "real" from such a window is not evidence.
    noisy_floor_db = -45.0

    def predict(self, x):
        chunks = self.chunks(x)
        if not chunks:
            raise ValueError("Nothing to analyse (empty input)")
        kept, noisy = [], 0
        for c, w in chunks:
            p = self.classify(c)
            if p < self.threshold and noise_floor_db(c, self.sample_rate) > self.noisy_floor_db:
                noisy += 1
                continue
            kept.append((p, w))
        if not kept:
            return 0.0, (f"Background sound in all {len(chunks)} window(s); this detector only hears "
                         "cloned voices in clean speech"), {"inconclusive": True}
        p = self.aggregate([p for p, _ in kept], [w for _, w in kept])
        detail = f"Mean of {len(kept)} window(s), max {max(p for p, _ in kept) * 100:.0f}%; threshold {self.threshold:g}"
        if noisy:
            detail += f"; skipped {noisy} window(s) with background sound"
        return p, detail, {}


@register
class VoiceWav2Vec2(_VoiceBase):
    id = "voice_wav2vec2"
    name = "Voice deepfake detector (wav2vec2)"
    repo = "garystafford/wav2vec2-deepfake-voice-detector"
    license = "Apache-2.0"
    size_mb = 1260
    recommended = True
    description = "wav2vec2 classifier that spots cloned or synthetic speech; analyses up to 12 ten-second windows."


@register
class VoiceMo(_VoiceBase):
    id = "voice_mo"
    name = "Voice deepfake detector (wav2vec2-base)"
    repo = "mo-thecreator/Deepfake-audio-detection"
    license = "Apache-2.0"
    size_mb = 380
    recommended = False
    description = "Smaller wav2vec2-base model for fake-speech detection; a lighter second opinion."
