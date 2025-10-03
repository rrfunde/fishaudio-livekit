# FishAudio LiveKit Plugin

Lightweight Python package that exposes the Fish Audio text-to-speech plugin for LiveKit Agents.

## Installation

```bash
pip install fishaudio-livekit
```

Or install straight from git while you are iterating:

```bash
pip install git+https://github.com/rrfunde/fishaudio-livekit.git@v0.1.0
```

## Usage

```python
from fishaudio_livekit import TTS as FishTTS

tts = FishTTS(backend="s1-mini", chunk_length=120)

async with tts.stream() as stream:
    stream.push_text("Hello from Fish Audio!")
    stream.flush()
    async for frame in stream:
        ...  # play or pipe audio
```

### Environment

- Set `FISHAUDIO_API_KEY` before launching your agent or worker.
- The package depends on `fish-audio-sdk`, `httpx`, and `httpx-ws`; these install automatically via pip.

## Development

```bash
pip install -e .[dev]
python -m build
```

Publish with `twine upload dist/*` or your internal registry tooling.
