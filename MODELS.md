# Traels — detection models

All models run locally and are off until switched on in Settings.
★ = recommended (enabled by "Turn on recommended").

| Used for | Model (Hugging Face repo) | What it does | On by default? | Licence | Size |
|---|---|---|---|---|---|
| Images | `abacusai/Smaug-AI-Image-Detector` | Vision-language model that checks 3 crops of the image; strongest on modern generators | ★ | Apache-2.0 | 880 MB |
| Images | `Thermostatic/community-forensics-frontier-detector-2026-08` | Small, fast ViT model fine-tuned in Aug 2026 on frontier generators | ★ | MIT | 44 MB |
| Images | `haywoodsloan/ai-image-detector-deploy` | SwinV2 classifier, artificial vs real; weak in our tests | Off | Apache-2.0 | 780 MB |
| Images | `s-gehring/detectra-openfake` | Tiny ViT trained on OpenFake; conservative, only flags above 95% | Off | CC-BY-NC (non-commercial) | 30 MB |
| Text | `fakespot-ai/roberta-base-ai-text-detection-v1` | RoBERTa classifier for AI-written vs human text; English only | ★ | Apache-2.0 | ~500 MB |
| Text | `desklib/ai-text-detector-v1.01` | DeBERTa-v3-large; ranks high on the RAID detection leaderboard | Off | MIT | 1.7 GB |
| Voice / speech | `garystafford/wav2vec2-deepfake-voice-detector` | Detects cloned voices and text-to-speech (trained on ElevenLabs, Polly and others) | ★ | Apache-2.0 | 1.26 GB |
| Voice / speech | `mo-thecreator/Deepfake-audio-detection` | Lighter wav2vec2 voice-deepfake detector | Off | Apache-2.0 | ~380 MB |
| Music | `AI-Music-Detection/ai_music_detection_large_60s` | Audio Spectrogram Transformer that detects AI-generated songs (Suno/Udio-style) | ★ | None declared | 363 MB |

## Which models run for each input

| You drop in… | Models that run |
|---|---|
| Image | The image models |
| Text | The text models |
| Audio file | The voice models and the music model |
| Video | 12 frames through the image models, and the soundtrack through the voice and music models (no good free video-specific model exists) |

Only models switched on in Settings run.

## How the 0–100 score is calculated

1. Each model gives a probability that the input is AI-made.
2. That probability is rescaled to 0–100 so the model's own threshold sits at 50, making models comparable.
3. Scores are averaged within each signal: picture, voice, music, text.
4. The final score is the most suspicious signal's average, so a cloned voice over real footage still counts as fake.
5. Bands: below 35 = "Likely real", 35–65 = "Unsure", above 65 = "Likely AI".

## Licence notes

- Detectra is non-commercial only.
- The music model declares no licence. Check with the author before any commercial use or public hosting.
