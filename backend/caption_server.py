"""ClassAccess caption prototype API.

Run this module when demonstrating the live-caption interface.  It is kept
separate from the speaker-enrolment entry point so the Firebase/extension
workflow remains untouched.  It uses the same local Faster-Whisper dependency
as ``stt-worker/process_audio.py`` and never substitutes invented text for a
recorded-audio demonstration.
"""
from __future__ import annotations

import asyncio
import os
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_AUDIO = ROOT / "main" / "test.wav"

app = FastAPI(title="ClassAccess Caption API", version="0.1.0")
# No authentication/cookies are used by this local prototype. Restrict this to
# known classroom origins before exposing it beyond a trusted LAN.
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


class StartRequest(BaseModel):
    title: str = "Accessibility in the Classroom"
    subject: str = "Inclusive learning"
    source: str = "recorded-demo"  # recorded-demo | audio-input
    audio_path: str | None = None


sessions: dict[str, dict[str, Any]] = {}
clients: dict[str, set[WebSocket]] = {}
workers: dict[str, asyncio.Task] = {}
model: Any = None


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def broadcast(session_id: str, event: dict[str, Any]) -> None:
    stale: list[WebSocket] = []
    for socket in clients.get(session_id, set()).copy():
        try:
            await socket.send_json(event)
        except Exception:
            stale.append(socket)
    for socket in stale:
        clients.get(session_id, set()).discard(socket)


async def status(session_id: str, state: str, message: str, audio: str) -> None:
    session = sessions[session_id]
    session.update(status=state, audioStatus=audio, updatedAt=now())
    await broadcast(session_id, {"type": "status", "sessionId": session_id, "state": state, "audioStatus": audio, "message": message, "timestamp": now()})


def transcribe(path: str):
    """Run local transcription outside FastAPI's event loop."""
    global model
    from faster_whisper import WhisperModel
    if model is None:
        model = WhisperModel(os.getenv("WHISPER_MODEL", "base"), device=os.getenv("WHISPER_DEVICE", "cpu"), compute_type="int8")
    segments, _ = model.transcribe(path, language=os.getenv("WHISPER_LANGUAGE", "en"), vad_filter=True)
    return [{"text": s.text.strip(), "start": s.start, "end": s.end} for s in segments if s.text.strip()]


async def run_recorded_demo(session_id: str, audio_path: str) -> None:
    try:
        await status(session_id, "connecting", "Loading recorded-audio demonstration…", "preparing")
        if not Path(audio_path).is_file():
            raise RuntimeError(f"Audio file not found: {audio_path}")
        await status(session_id, "listening", "Transcribing recorded audio locally…", "receiving")
        try:
            output = await asyncio.to_thread(transcribe, audio_path)
        except ModuleNotFoundError as error:
            raise RuntimeError("Faster-Whisper is not installed. Install stt-worker/requirements.txt to run recorded audio.") from error
        for position, item in enumerate(output):
            if sessions[session_id]["status"] == "stopped":
                return
            caption = {"id": f"{session_id}:{position}", "text": item["text"], "timestamp": item["start"], "final": True, "speaker": None}
            sessions[session_id]["captions"].append(caption)
            await broadcast(session_id, {"type": "caption", "sessionId": session_id, **caption})
            # Preserve the feeling of a stream without misrepresenting chunked inference.
            await asyncio.sleep(min(max(item["end"] - item["start"], 0.35), 1.5))
        await status(session_id, "stopped", "Recorded-audio demonstration complete.", "stopped")
    except Exception as error:
        await status(session_id, "error", str(error), "unavailable")


async def publish_transcription(session_id: str, path: str, speaker: str | None = None) -> int:
    """Transcribe one submitted microphone recording and broadcast final captions."""
    output = await asyncio.to_thread(transcribe, path)
    start_at = len(sessions[session_id]["captions"])
    # Browser microphone chunks each begin at zero. Keep their caption times
    # chronological in the shared transcript instead of repeating 00:00.
    time_offset = sessions[session_id].get("nextTimestamp", 0.0)
    final_end = time_offset
    for offset, item in enumerate(output):
        caption = {
            "id": f"{session_id}:{start_at + offset}", "text": item["text"],
            "timestamp": time_offset + item["start"], "final": True, "speaker": speaker,
        }
        final_end = max(final_end, time_offset + item["end"])
        sessions[session_id]["captions"].append(caption)
        await broadcast(session_id, {"type": "caption", "sessionId": session_id, **caption})
    sessions[session_id]["nextTimestamp"] = final_end
    return len(output)


@app.get("/health")
async def health():
    return {"status": "ok", "service": "classaccess-caption-api", "whisperReady": model is not None}


@app.get("/api/captions/devices")
async def devices():
    """Report audio source availability without opening a recording stream."""
    try:
        import sounddevice as sd
        return {"available": True, "devices": [{"name": d["name"], "index": i} for i, d in enumerate(sd.query_devices()) if d["max_input_channels"] > 0]}
    except Exception as error:
        return {"available": False, "reason": f"Audio capture unavailable: {error}"}


@app.post("/api/captions/sessions")
async def start_session(request: StartRequest):
    if request.source not in {"recorded-demo", "microphone-questions"}:
        raise HTTPException(400, "source must be recorded-demo or microphone-questions")
    session_id = str(uuid.uuid4())
    source_name = "recorded-audio demonstration" if request.source == "recorded-demo" else "shared microphone questions"
    session = {"id": session_id, "title": request.title, "subject": request.subject, "source": source_name, "status": "connecting", "audioStatus": "preparing", "startedAt": now(), "captions": []}
    sessions[session_id] = session
    if request.source == "recorded-demo":
        workers[session_id] = asyncio.create_task(run_recorded_demo(session_id, request.audio_path or str(DEFAULT_AUDIO)))
    else:
        await status(session_id, "listening", "Shared Q&A is ready. Microphone recordings will be transcribed locally.", "waiting")
    return session


@app.get("/api/captions/sessions/{session_id}")
async def get_session(session_id: str):
    if session_id not in sessions:
        raise HTTPException(404, "Session not found")
    return sessions[session_id]


@app.post("/api/captions/sessions/{session_id}/stop")
async def stop_session(session_id: str):
    if session_id not in sessions:
        raise HTTPException(404, "Session not found")
    task = workers.get(session_id)
    if task and not task.done(): task.cancel()
    await status(session_id, "stopped", "Session stopped by presenter.", "stopped")
    return sessions[session_id]


@app.post("/api/captions/sessions/{session_id}/microphone")
async def transcribe_microphone(session_id: str, audio: UploadFile = File(...), speaker: str | None = None):
    """Accept a browser MediaRecorder clip, then publish its local transcription.

    A clip is processed only after the user turns the microphone off. This is
    deliberately labelled chunked transcription rather than live partial STT.
    """
    if session_id not in sessions:
        raise HTTPException(404, "Session not found")
    if sessions[session_id]["status"] == "stopped":
        raise HTTPException(409, "Session has stopped")
    suffix = Path(audio.filename or "question.webm").suffix or ".webm"
    temp_path = ""
    try:
        with tempfile.NamedTemporaryFile(prefix="classaccess-question-", suffix=suffix, delete=False) as temp:
            temp_path = temp.name
            while chunk := await audio.read(1024 * 1024):
                temp.write(chunk)
        await status(session_id, "listening", "Transcribing a submitted microphone question…", "processing")
        count = await publish_transcription(session_id, temp_path, speaker)
        await status(session_id, "listening", "Ready for the next question.", "waiting")
        return {"status": "complete", "captionCount": count}
    except Exception as error:
        await broadcast(session_id, {"type": "error", "sessionId": session_id, "message": f"Question transcription failed: {error}"})
        raise HTTPException(500, "Could not transcribe microphone recording") from error
    finally:
        if temp_path:
            Path(temp_path).unlink(missing_ok=True)


@app.websocket("/ws/captions/{session_id}")
async def caption_socket(websocket: WebSocket, session_id: str):
    await websocket.accept()
    if session_id not in sessions:
        await websocket.send_json({"type": "error", "message": "Unknown session"})
        await websocket.close(code=4404)
        return
    clients.setdefault(session_id, set()).add(websocket)
    session = sessions[session_id]
    await websocket.send_json({"type": "status", "sessionId": session_id, "state": session["status"], "audioStatus": session["audioStatus"], "message": "Caption connection established.", "timestamp": now()})
    for caption in session["captions"]:
        await websocket.send_json({"type": "caption", "sessionId": session_id, **caption})
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        clients[session_id].discard(websocket)
