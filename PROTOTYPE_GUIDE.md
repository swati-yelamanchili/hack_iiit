# ClassAccess caption prototype

The original Firebase transcript flow and browser speaker-recognition service
remain in place. The accessibility dashboard uses the small local caption API
in `backend/caption_server.py`; this avoids needing Firebase credentials for a
live demo.

## Run

Create a Python virtual environment, then install the demo dependencies:

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r backend/requirements-caption.txt
uvicorn backend.caption_server:app --host 127.0.0.1 --port 8000
```

In another terminal:

```bash
cd minimal-frontend
npm install
npm run dev
```

Open `http://localhost:3000` and press **Turn microphone on**. This single
button automatically creates the classroom session the first time it is used.
While it remains on, the browser sends approximately 2.2-second audio chunks
for local transcription and broadcasts each finalized caption over WebSocket.
Use **Copy classroom link** once, then every teacher or student who opens that
link has the same microphone on/off button. This is low-latency chunked
captioning—not word-by-word partial inference—so a caption normally arrives
shortly after its audio chunk ends.

For classmates on another device, run the API with `--host 0.0.0.0` and start
the frontend with `NEXT_PUBLIC_CAPTION_API_URL=http://<presenter-LAN-IP>:8000`.
They must open the frontend from that same presenter host (not their own
`localhost`); microphone access requires a secure context or localhost, so use
a trusted HTTPS setup when participants record from separate devices.

**Run recorded-audio demo** instead transcribes `main/test.wav` locally with
Faster-Whisper. It is explicitly labelled as a recorded-audio demonstration.

`WHISPER_MODEL`, `WHISPER_DEVICE` (default `cpu`), and `WHISPER_LANGUAGE` can
be set before starting the API. The first execution may download the selected
model. It does not use Firebase or an external transcription API.

## Audio sources and limitations

The existing `backend/audio_capture.py` can enumerate PipeWire/PulseAudio
monitor inputs and is used by the existing recognition service. Route Zoom's
output to a monitor source in `pavucontrol`, configure that service as in the
root README, and obtain meeting consent before capture. The caption prototype
server intentionally reports live source capture as unavailable rather than
pretending to receive Zoom audio; its verified fallback is the recorded WAV
pipeline above. Faster-Whisper must be installed for it to produce captions.

The browser archive is intentionally transient: it shows the current session,
not a persistent history. Summary generation is similarly shown as unavailable
unless the existing Firebase/Perplexity processing path is configured.

## API contract

- `GET /health` returns caption API health.
- `POST /api/captions/sessions` starts a recorded-audio session.
- `POST /api/captions/sessions/{id}/stop` stops and cleans up a session.
- `POST /api/captions/sessions/{id}/microphone` accepts an audio clip from the
  browser microphone and broadcasts its local transcription.
- `GET /api/captions/sessions/{id}` returns session state and final captions.
- `WS /ws/captions/{id}` emits `status` and finalized `caption` events.

Caption events contain `type`, `sessionId`, stable `id`, `text`, `timestamp`,
`final`, and `speaker` (always null unless a real diarization integration is
added). Stable ids prevent duplicate display after reconnecting.
