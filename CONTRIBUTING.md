# Contributing to Traels

Thanks for helping. Issues and pull requests are welcome.

## Development setup

Requirements: macOS 14+, Xcode 16 / Swift 6, [uv](https://docs.astral.sh/uv/).

```sh
swift run Traels            # runs the app against ./backend
scripts/build-app.sh        # builds build/Traels.app
```

The app talks to `backend/worker.py` over JSON lines on stdin/stdout. The protocol is documented in
[ARCHITECTURE.md](ARCHITECTURE.md). The worker's Python dependencies live in its inline
`# /// script` header, and uv installs them on first run.

## Tests

`backend/test_worker.py` is an end-to-end harness that drives the worker (stdlib only):

```sh
python3 backend/test_worker.py models text           # quick
python3 backend/test_worker.py                        # everything (downloads models)
TRAELS_SAMPLES=/path/to/samples python3 backend/test_worker.py image video
```

The image and video stages need a folder with `1_fake.jpg` (AI-generated) and `1_original.jpg`
(a real photo).

Lint the backend with `uvx ruff check --select F,E9 backend`.

## Adding a detector

Most Hugging Face classifiers need only a subclass of `HFPipelineDetector`
(`backend/detectors/hf.py`), registered in the module for its kind:

```python
@register
class MyTextDetector(HFPipelineDetector):
    id = "my_text"
    name = "My AI text detector"
    kind = "text"                       # image | audio | music | text
    task = "text-classification"
    repo = "someone/some-detector"
    license = "Apache-2.0"
    size_mb = 500
    threshold = 0.5
    ai_labels = {"ai", "fake", "label_1"}  # lowercase labels counted as "AI"
    description = "One sentence shown in Settings."
```

Please include in the PR:

- the model's license, and a note if it's non-commercial or undeclared;
- a few results on real and AI samples (paste the `test_worker.py` output);
- an update to the table in [MODELS.md](MODELS.md).

Custom models (ONNX, custom code) subclass `Detector` directly. See `backend/detectors/image.py`.

## Pull requests

- Keep changes focused. One feature or fix per PR.
- Match the surrounding code style: SwiftUI in `Sources/Traels`, plain Python 3.11+ in `backend`.
- Analysis must stay offline. Only model downloads and user-requested link fetches may use the network.
