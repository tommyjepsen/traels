"""Base class for detectors backed by a standard `transformers.pipeline(...)` classifier."""
from __future__ import annotations

from . import Detector, pick_device, log


class HFPipelineDetector(Detector):
    """Subclass and set: id, name, kind, repo, license, size_mb, description, task, ai_labels.

        @register
        class MyTextDetector(HFPipelineDetector):
            id = "my-text"; kind = "text"; task = "text-classification"
            ai_labels = {"ai", "fake", "label_1"}   # lowercase; probabilities of these are summed
            ...

    Inputs: text -> str, audio/music -> float32 mono numpy at 16 kHz (resampled to
    `sample_rate` if that differs). Long inputs are chunked and aggregated (token-weighted mean
    for text, duration-weighted mean for audio) - override `chunks()` / `aggregate()` to customise.
    """
    task = "text-classification"
    ai_labels: set[str] = set()
    max_tokens = 400          # text chunk size in tokens (excl. special tokens)
    chunk_seconds = 10.0      # audio chunk length
    sample_rate = 16000       # sample rate the model expects
    max_chunks = 24
    pipeline_kwargs: dict = {}
    use_device = True
    min_chunk_seconds = 1.0
    call_kwargs: dict = {}

    def _load(self):
        from transformers import pipeline
        kw = dict(self.pipeline_kwargs)
        if self.use_device:
            kw["device"] = pick_device()
        try:
            self.pipe = pipeline(self.task, model=self.repo, **kw)
        except Exception as e:  # noqa: BLE001
            if "device" not in kw or kw["device"] == "cpu":
                raise
            log(f"{self.id}: device {kw['device']} failed ({e}); using cpu")
            kw["device"] = "cpu"
            self.pipe = pipeline(self.task, model=self.repo, **kw)

    # ---- chunking ----------------------------------------------------
    def chunks(self, x):
        """-> list of (chunk, weight)"""
        if self.kind == "text":
            from . import text as textmod
            return textmod.chunk_text(x, self.pipe.tokenizer, self.max_tokens, self.max_chunks)
        from . import audio as audiomod
        sr = self.sample_rate
        if sr != audiomod.SAMPLE_RATE:
            x = audiomod.resample(x, audiomod.SAMPLE_RATE, sr)
        return audiomod.chunk_audio(x, sr, self.chunk_seconds, self.max_chunks, self.min_chunk_seconds)

    def classify(self, chunk) -> float:
        inp = chunk if self.kind == "text" else {"raw": chunk, "sampling_rate": self.sample_rate}
        out = self.pipe(inp, top_k=None, **self.call_kwargs)
        if out and isinstance(out[0], list):
            out = out[0]
        return float(sum(o["score"] for o in out if str(o["label"]).lower() in self.ai_labels))

    def aggregate(self, ps, weights):
        tw = sum(weights) or 1.0
        return sum(p * w for p, w in zip(ps, weights)) / tw

    def predict(self, x):
        chunks = self.chunks(x)
        if not chunks:
            raise ValueError("Nothing to analyse (empty input)")
        ps = [self.classify(c) for c, _ in chunks]
        p = self.aggregate(ps, [w for _, w in chunks])
        unit = "chunk" if self.kind == "text" else "window"
        return p, f"Mean of {len(ps)} {unit}(s), max {max(ps) * 100:.0f}%; threshold {self.threshold:g}", {"chunk_p": ps}
