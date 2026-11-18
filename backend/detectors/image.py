"""Image detectors. Preprocessing and calibration follow each model's Hugging Face model card and config."""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

from . import Detector, register, pick_device, log

IMAGENET_MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
IMAGENET_STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)


def sigmoid(x: float) -> float:
    return 1 / (1 + math.exp(-x))


def load_rgb(path) -> Image.Image:
    return ImageOps.exif_transpose(Image.open(path)).convert("RGB")


def as_rgb(img: Image.Image) -> Image.Image:
    return ImageOps.exif_transpose(img).convert("RGB")


def short_edge_center_crop(img, short_edge, crop):
    w, h = img.size
    s = short_edge / min(w, h)
    img = img.resize((round(w * s), round(h * s)), Image.BILINEAR)
    w, h = img.size
    x, y = (w - crop) // 2, (h - crop) // 2
    return img.crop((x, y, x + crop, y + crop))


def to_tensor(img):
    a = (np.asarray(img, dtype=np.float32) / 255.0 - IMAGENET_MEAN) / IMAGENET_STD
    return a.transpose(2, 0, 1)[None].astype(np.float32)


def _ort_session(path):
    import onnxruntime as ort
    return ort.InferenceSession(path, providers=["CPUExecutionProvider"])


@register
class Smaug(Detector):
    id = "smaug"
    name = "Smaug AI Image Detector"
    kind = "image"
    repo = "abacusai/Smaug-AI-Image-Detector"
    license = "Apache-2.0"
    size_mb = 880
    recommended = True
    threshold = 0.5
    description = "Vision-language model from Abacus.AI that looks at three crops of the image; strong on modern generators."

    def _load(self):
        from huggingface_hub import snapshot_download
        path = snapshot_download(self.repo, local_files_only=True)
        if path not in sys.path:
            sys.path.insert(0, path)
        from smaug_image_detect import SmaugImageDetect, views
        self.detector, self.views = SmaugImageDetect.from_pretrained(path), views

    def predict(self, img):
        crops = self.views(img, 3)
        logits = self.detector.st.encode([{"image": c} for c in crops], convert_to_tensor=True).float().view(-1).tolist()
        mean = sum(logits) / len(logits)
        return sigmoid(mean), f"Mean of {len(crops)} crops; threshold {self.threshold:g}", {"logit": mean}


@register
class Thermostatic(Detector):
    id = "thermostatic"
    name = "Community Forensics (Thermostatic)"
    kind = "image"
    repo = "Thermostatic/community-forensics-frontier-detector-2026-08"
    license = "MIT"
    size_mb = 44
    recommended = True
    threshold = 0.65
    files = ["community_forensics_frontier_fp16.onnx", "config.json"]
    description = "Small, fast ViT detector fine-tuned in 2026 on frontier image generators."

    def _load(self):
        from huggingface_hub import hf_hub_download
        cfg = json.loads(Path(hf_hub_download(self.repo, "config.json")).read_text())
        self.intercept = cfg["calibration"]["intercept"]
        self.threshold = cfg["target_probability_threshold"]
        self.sess = _ort_session(hf_hub_download(self.repo, "community_forensics_frontier_fp16.onnx"))
        self.input = self.sess.get_inputs()[0].name

    def predict(self, img):
        raw = float(self.sess.run(None, {self.input: to_tensor(short_edge_center_crop(img, 440, 384))})[0].reshape(-1)[0])
        return sigmoid(raw + self.intercept), f"Centre crop 384px; threshold {self.threshold:g}", {"logit": raw}


@register
class Haywoodsloan(Detector):
    id = "haywoodsloan"
    name = "SwinV2 AI Image Detector (haywoodsloan)"
    kind = "image"
    repo = "haywoodsloan/ai-image-detector-deploy"
    license = "Apache-2.0"
    size_mb = 780
    recommended = False
    threshold = 0.5
    files = ["config.json", "model.safetensors", "preprocessor_config.json"]
    description = "Swin Transformer V2 image classifier trained to separate artificial from real images."

    def _load(self):
        import torch
        from transformers import AutoImageProcessor, AutoModelForImageClassification
        self.torch = torch
        self.proc = AutoImageProcessor.from_pretrained(self.repo)
        self.model = AutoModelForImageClassification.from_pretrained(self.repo).eval()
        self.device = pick_device()
        if self.device != "cpu":
            try:
                self.model.to(self.device)
            except Exception as e:  # noqa: BLE001
                log("device move failed, using cpu:", e)
                self.device = "cpu"
        self.ai = {v: int(k) for k, v in self.model.config.id2label.items()}["artificial"]

    def _run(self, img):
        inputs = {k: v.to(self.device) for k, v in self.proc(images=img, return_tensors="pt").items()}
        with self.torch.no_grad():
            return self.model(**inputs).logits[0].float().cpu()

    def predict(self, img):
        try:
            logits = self._run(img)
        except Exception as e:  # noqa: BLE001
            if self.device == "cpu":
                raise
            log("falling back to cpu:", e)
            self.model.to("cpu")
            self.device = "cpu"
            logits = self._run(img)
        p = logits.softmax(-1).tolist()[self.ai]
        return p, f"SwinV2 classifier on {self.device}; threshold {self.threshold:g}", {"logits": logits.tolist()}


@register
class Detectra(Detector):
    id = "detectra"
    name = "Detectra OpenFake"
    kind = "image"
    repo = "s-gehring/detectra-openfake"
    license = "CC-BY-NC-4.0 (non-commercial)"
    size_mb = 30
    recommended = False
    threshold = 0.95
    files = ["model.onnx"]
    description = "Tiny ViT detector trained on OpenFake (CC-BY-NC, non-commercial use only); conservative, flags from 95%."

    def _load(self):
        from huggingface_hub import hf_hub_download
        self.sess = _ort_session(hf_hub_download(self.repo, "model.onnx"))
        self.input = self.sess.get_inputs()[0].name

    def _logit(self, img, short_edge):
        return float(self.sess.run(None, {self.input: to_tensor(short_edge_center_crop(img, short_edge, 384))})[0].reshape(-1)[0])

    def predict(self, img):
        raw = self._logit(img, 440)
        passes = {"440": raw}
        p = sigmoid(0.552 * raw + 0.1933)
        if 0.5 <= p < 0.95:
            for edge in (576, 768):
                if min(img.size) >= edge:
                    passes[str(edge)] = self._logit(img, edge)
            p = max(sigmoid(0.552 * l + 0.1933) for l in passes.values())
        return p, f"Platt-scaled, {len(passes)} pass(es); threshold {self.threshold:g}", {"pass_logits": passes}
