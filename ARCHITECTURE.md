# Traels — local AI/fake content checker for macOS

Drop, paste or drag an image, video, audio file or text onto the window. It returns one
0–100 score ("how likely is this AI-generated / fake") with a big progress-bar visual and a
plain-language explanation underneath, plus a per-model breakdown.

Everything runs locally. No model is downloaded by default: the user switches models on in
Settings, which downloads them once from the Hugging Face Hub. Analysis itself never touches
the network (`HF_HUB_OFFLINE=1`).

## Layout

```
Package.swift                 SwiftPM, macOS 14+, executable target "Traels"
Sources/Traels/               SwiftUI app
backend/worker.py             Python inference worker (uv inline-script deps, python >=3.11,<3.14)
backend/detectors/            one module per detector family (image, text, audio, music, video)
scripts/build-app.sh          swift build -c release + assemble build/Traels.app (copies backend/ into Resources)
```

## Process model

The app starts one long-lived worker:

```
uv run --script <Resources>/backend/worker.py
```

`uv` is located via `/opt/homebrew/bin/uv`, `/usr/local/bin/uv`, `~/.local/bin/uv`,
`~/.cargo/bin/uv`, then `PATH` from a login shell (`/bin/zsh -lc 'command -v uv'`). If uv is
missing the app shows an onboarding card with the install command.

Environment passed to the worker: `HF_HUB_OFFLINE=1` (worker clears it only inside the
`download` command), `PYTHONUNBUFFERED=1`, `TOKENIZERS_PARALLELISM=false`.
The worker uses the default Hugging Face cache (`~/.cache/huggingface/hub`) so models already
downloaded elsewhere are reused.

## Wire protocol — JSON Lines over stdin/stdout

One JSON object per line. stderr is free-form logging (the app keeps the last ~200 lines for a
"Show log" view). Every request has a string `id`; every response line echoes it.

### Requests (app → worker)

```json
{"id":"1","cmd":"models"}
{"id":"2","cmd":"download","model":"smaug"}
{"id":"3","cmd":"delete","model":"smaug"}
{"id":"4","cmd":"analyze","kind":"image","path":"/tmp/x.png","models":["smaug","thermostatic"]}
{"id":"5","cmd":"analyze","kind":"text","text":"...","models":["..."]}
{"id":"6","cmd":"cancel","target":"4"}
{"id":"7","cmd":"fetch","url":"https://…","video_sites":false}
```

`fetch` downloads a pasted or dropped link into a temp dir (the only deliberate network use besides
model downloads): direct media is saved as-is; for an HTML page the worker takes the media the page
declares (og:video, og:image, twitter:*, then inline `<video>`). Links on known video sites
(YouTube, TikTok, X, …) go to `backend/video_sites.py`, a separate uv script with yt-dlp + Deno that
only runs when the user turns on "Video sites" (Settings → General); split video/audio downloads are
joined with PyAV, no ffmpeg needed. The app then sends a normal `analyze` with
`"source":{"host":"youtube.com","platform":true}`, which adds a Source fact and a re-compression note.

`kind` ∈ `image | video | audio | text`. For text the app sends `text` inline; for the rest a
file `path` (pasted images are written to a temp PNG first). `models` is the list of enabled,
installed model ids applicable to that kind; for `video` the worker also uses applicable image
and audio models on frames / the audio track.

### Responses (worker → app)

```json
{"id":"0","type":"ready","version":"1"}                       // once at startup, id "0"
{"id":"1","type":"models","models":[ModelInfo,...]}
{"id":"2","type":"progress","fraction":0.42,"message":"Downloading model.onnx"}
{"id":"2","type":"done"}                                       // download / delete finished
{"id":"4","type":"progress","fraction":0.5,"message":"Running Smaug (2/3)"}
{"id":"4","type":"result","result":AnalysisResult}
{"id":"4","type":"error","message":"human readable"}
{"id":"7","type":"fetched","file":{"path":"/tmp/…/video.mkv","kind":"video","title":"…",
                                   "source":"youtube.com","platform":true,"thumb":"/tmp/…/thumb.jpg"}}
```

`ModelInfo`:
```json
{"id":"smaug","name":"Smaug AI Image Detector","kind":"image",
 "repo":"abacusai/Smaug-AI-Image-Detector","license":"Apache-2.0",
 "size_mb":350,"installed":true,"description":"one sentence","recommended":true}
```
`kind` ∈ `image | video | audio | music | text`. (`music` models analyse audio files.)

`AnalysisResult`:
```json
{"kind":"image","score":83.4,                // 0–100, combined
 "verdict":"likely_ai",                      // likely_real | uncertain | likely_ai
 "headline":"Likely AI-generated",
 "summary":"2 of 3 detectors think this image was made by AI. ...",
 "models":[{"id":"smaug","name":"Smaug","score":91.2,"verdict":"likely_ai",
            "detail":"Mean of 3 crops; threshold 50"}],
 "facts":[{"label":"Resolution","value":"1024×1024"},{"label":"EXIF camera","value":"none"}],
 "seconds":2.3}
```

### Scoring

Each detector returns its own calibrated probability p∈[0,1] and its recommended threshold t.
To make detectors comparable it is mapped to a 0–100 score where the model's threshold sits at 50:
`s = 50 * p/t` if p < t else `50 + 50 * (p - t)/(1 - t)`.
Scores are averaged within each signal (picture = image/video models, voice, music, text), and the
combined score is the most suspicious signal's average — a cloned voice over real footage is still fake. Verdict bands: `< 35` likely_real,
`35–65` uncertain, `> 65` likely_ai. Headline per kind, e.g. image: "Likely a real photo" /
"Can't tell" / "Likely AI-generated"; text: "Likely written by a human" / … / "Likely AI-written".

Facts are cheap non-ML signals (image: resolution, format, EXIF camera make/model presence,
C2PA/"Made with AI" metadata if trivially detectable via XMP strings; text: word count; audio:
duration, sample rate; video: duration, resolution, frames sampled).

## App UX

* Single window, ~520×640, one big drop zone: "Drop an image, video, audio or text — or paste (⌘V)".
* Accepts file URLs, images (NSImage/TIFF/PNG data), and plain strings via drag & drop and ⌘V.
  Type is decided by UTType (image / movie / audio / text file); a dropped/pasted string is text.
* While running: thumbnail/preview of the input + determinate progress from worker events.
* Result: huge number "83", a horizontal gauge 0–100 with green→amber→red zones and a marker,
  the headline, summary paragraph, then a disclosure "Details" with per-model bars and facts.
* "Check another" resets. Recent results not persisted (privacy) in v1.
* If no enabled model covers the dropped kind: friendly card "No text detector enabled" with a
  button that opens Settings on the Models tab.
* Settings (⌘,): Models tab grouped by kind (Images, Video, Audio/Voice, Music, Text), each row:
  name, license, size, description, Toggle. Turning on → downloads with an inline progress bar,
  then enabled. Turning off → disabled (files kept) + small "Remove files" button to delete.
  Enabled set persisted in UserDefaults (`enabledModels`). General tab: show uv/worker status,
  "Show log", "Restart worker".
