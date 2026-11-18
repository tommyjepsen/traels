"""Text (AI-written) detectors + token chunking helper.

Text detectors. Subclass hf.HFPipelineDetector for standard classifiers.
"""
from __future__ import annotations

from . import Detector, register  # noqa: F401
from .hf import HFPipelineDetector  # noqa: F401


def chunk_text(text: str, tokenizer, max_tokens: int = 400, max_chunks: int = 24):
    """Split text into windows of <= max_tokens tokens -> [(chunk_str, weight_tokens)]."""
    text = text.strip()
    if not text:
        return []
    ids = tokenizer(text, add_special_tokens=False, truncation=False)["input_ids"]
    parts = [ids[i:i + max_tokens] for i in range(0, len(ids), max_tokens)]
    if len(parts) > 1 and len(parts[-1]) < 20:  # tiny tail adds noise
        parts.pop()
    if len(parts) > max_chunks:
        step = len(parts) / max_chunks
        parts = [parts[int(i * step)] for i in range(max_chunks)]
    return [(tokenizer.decode(p), len(p)) for p in parts if p]


def clean_text(t: str) -> str:
    import re
    import unicodedata
    t = unicodedata.normalize("NFKC", t).replace("\u200b", "")
    return re.sub(r"[ \t\f\v]+", " ", re.sub(r"\n{3,}", "\n\n", t)).strip()


@register
class Fakespot(HFPipelineDetector):
    id = "fakespot"
    name = "Fakespot AI text detector"
    kind = "text"
    task = "text-classification"
    repo = "fakespot-ai/roberta-base-ai-text-detection-v1"
    license = "Apache-2.0"
    size_mb = 500
    recommended = True
    threshold = 0.5
    ai_labels = {"ai"}
    max_tokens = 500
    max_chunks = 8
    call_kwargs = {"truncation": True, "max_length": 512}
    description = "RoBERTa classifier for AI-written text. English only; short texts (under ~50 words) are unreliable."

    def predict(self, x):
        return super().predict(clean_text(x))


@register
class Desklib(Detector):
    id = "desklib"
    name = "Desklib AI text detector"
    kind = "text"
    repo = "desklib/ai-text-detector-v1.01"
    license = "MIT"
    size_mb = 1740
    recommended = False
    threshold = 0.5
    max_len = 768
    description = "Large DeBERTa-v3 detector of AI-written text (English); heavier but often stronger on long passages."

    def _load(self):
        import torch
        import torch.nn as nn
        from huggingface_hub import snapshot_download
        from transformers import AutoConfig, AutoModel, AutoTokenizer, PreTrainedModel
        from . import pick_device

        class DesklibAIDetectionModel(PreTrainedModel):  # from the model card
            config_class = AutoConfig

            def __init__(self, config):
                super().__init__(config)
                self.model = AutoModel.from_config(config)
                self.classifier = nn.Linear(config.hidden_size, 1)
                self.post_init()

            def forward(self, input_ids, attention_mask=None):
                h = self.model(input_ids, attention_mask=attention_mask)[0]
                m = attention_mask.unsqueeze(-1).expand(h.size()).float()
                pooled = (h * m).sum(dim=1) / m.sum(dim=1).clamp(min=1e-9)
                return self.classifier(pooled)

        self.torch = torch
        path = snapshot_download(self.repo, local_files_only=True)
        self.tok = AutoTokenizer.from_pretrained(path)
        self.model = DesklibAIDetectionModel.from_pretrained(path).eval()
        self.device = pick_device()
        try:
            self.model.to(self.device)
        except Exception:
            self.device = "cpu"
            self.model.to("cpu")

    def predict(self, x):
        chunks = chunk_text(clean_text(x), self.tok, self.max_len - 8, 8)
        if not chunks:
            raise ValueError("Nothing to analyse (empty input)")
        ps = []
        for c, _ in chunks:
            enc = self.tok(c, truncation=True, max_length=self.max_len, return_tensors="pt").to(self.device)
            with self.torch.no_grad():
                logit = self.model(enc["input_ids"], attention_mask=enc["attention_mask"]).reshape(-1)[0].float().item()
            ps.append(1 / (1 + self.torch.exp(self.torch.tensor(-logit)).item()))
        tw = sum(w for _, w in chunks)
        p = sum(p * w for p, (_, w) in zip(ps, chunks)) / tw
        return p, f"Mean of {len(ps)} chunk(s), max {max(ps) * 100:.0f}%; threshold {self.threshold:g}", {}
