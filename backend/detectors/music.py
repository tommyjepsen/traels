"""Music (AI-generated song) detectors. They analyse audio files; decoding helpers live in audio.py.

New music detectors: subclass hf.HFPipelineDetector with task="audio-classification",
kind="music", sample_rate=<model rate>, and decorate with @register.
"""
from __future__ import annotations

from . import register
from .hf import HFPipelineDetector

AST_FE_REPO = "MIT/ast-finetuned-audioset-10-10-0.4593"


@register
class MusicAST(HFPipelineDetector):
    id = "music_ast"
    name = "AI Music Detection (AST)"
    kind = "music"
    task = "audio-classification"
    repo = "AI-Music-Detection/ai_music_detection_large_60s"
    license = "none declared by the author"
    size_mb = 363
    recommended = True
    threshold = 0.5
    ai_labels = {"ai_generated", "ai", "fake"}
    extra_files = [(AST_FE_REPO, ["preprocessor_config.json"])]
    chunk_seconds = 60.0
    min_chunk_seconds = 10.0
    max_chunks = 3
    sample_rate = 16000
    description = ("Audio Spectrogram Transformer that flags AI-generated songs (up to three 60 s windows). "
                   "The author has not declared a license.")

    def _load(self):
        from transformers import ASTFeatureExtractor, pipeline
        from . import pick_device
        fe = ASTFeatureExtractor.from_pretrained(AST_FE_REPO)
        fe.max_length = 6000
        dev = pick_device()
        try:
            self.pipe = pipeline(self.task, model=self.repo, feature_extractor=fe, device=dev)
        except Exception:
            self.pipe = pipeline(self.task, model=self.repo, feature_extractor=fe, device="cpu")
