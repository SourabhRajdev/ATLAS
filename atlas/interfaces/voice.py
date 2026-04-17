"""Voice interface — push-to-talk and continuous modes.

Uses faster-whisper for local STT, macOS say for TTS (ElevenLabs optional).
Audio capture via pyaudio with silence detection.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import tempfile
import wave

from rich.console import Console

logger = logging.getLogger("atlas.voice")
console = Console()

STOP_WORDS = {"stop", "quit", "exit", "goodbye", "bye"}


async def check_mic_permission() -> bool:
    """Check if microphone access is granted."""
    try:
        import pyaudio
        p = pyaudio.PyAudio()
        stream = p.open(
            format=pyaudio.paInt16, channels=1,
            rate=16000, input=True,
            frames_per_buffer=1024,
        )
        stream.read(1024, exception_on_overflow=False)
        stream.close()
        p.terminate()
        return True
    except Exception as e:
        logger.warning("Mic permission check failed: %s", e)
        return False


class VoiceSession:
    def __init__(self, orch, session_id: str, config) -> None:
        self.orch = orch
        self.session_id = session_id
        self.config = config
        self._whisper_model = None

    async def run_once(self) -> None:
        if not await check_mic_permission():
            console.print("[warning]Microphone permission denied.[/warning]")
            console.print("Go to: System Settings → Privacy → Microphone")
            console.print("Add Terminal (or your terminal app) to the list.")
            return

        console.print("[dim]🎤 Listening... (speak now)[/dim]")
        audio = await asyncio.to_thread(self._record_until_silence)
        if not audio:
            console.print("[warning]No audio detected. Check:[/warning]")
            console.print("  1. Microphone permission in System Settings")
            console.print("  2. Correct input device selected")
            console.print("  3. Try speaking louder")
            return
        text = await self._transcribe(audio)
        if not text or not text.strip():
            console.print("[warning]Could not transcribe.[/warning]")
            return
        console.print(f"[dim]> {text}[/dim]")
        response, _ = await self.orch.process(text, self.session_id)
        console.print(f"[bold blue]ATLAS:[/bold blue] {response}")
        await self._speak(response)

    async def run_continuous(self) -> None:
        if not await check_mic_permission():
            console.print("[warning]Microphone permission denied.[/warning]")
            console.print("Go to: System Settings → Privacy → Microphone")
            console.print("Add Terminal (or your terminal app) to the list.")
            return

        console.print("[dim]Continuous voice mode. Say 'stop' to exit.[/dim]")
        while True:
            console.print("[dim]🎤 Listening... (speak now)[/dim]")
            audio = await asyncio.to_thread(self._record_until_silence)
            if not audio:
                continue
            text = await self._transcribe(audio)
            if not text or not text.strip():
                continue
            # Check first word — Whisper sometimes appends noise after stop words
            first_word = text.lower().strip().split()[0].rstrip(".,!?")
            if first_word in STOP_WORDS or text.lower().strip() in STOP_WORDS:
                console.print("[info]Voice mode ended.[/info]")
                break
            console.print(f"[dim]> {text}[/dim]")
            response, _ = await self.orch.process(text, self.session_id)
            console.print(f"[bold blue]ATLAS:[/bold blue] {response}")
            await self._speak(response)

    def _record_until_silence(self) -> bytes:
        import pyaudio  # type: ignore
        import numpy as np  # type: ignore

        RATE = 16000
        CHUNK = 1024
        SILENCE_THRESHOLD = 200   # lowered from 500 for better sensitivity
        SILENCE_DURATION = 1.0    # wait longer before cutting off
        MAX_DURATION = 15.0

        p = pyaudio.PyAudio()
        stream = p.open(
            format=pyaudio.paInt16, channels=1,
            rate=RATE, input=True, frames_per_buffer=CHUNK,
        )

        frames: list[bytes] = []
        silent_chunks = 0
        speaking = False
        max_chunks = int(RATE / CHUNK * MAX_DURATION)

        for i in range(max_chunks):
            data = stream.read(CHUNK, exception_on_overflow=False)
            frames.append(data)
            amplitude = np.frombuffer(data, dtype=np.int16)
            volume = int(np.abs(amplitude).mean())

            # Live volume feedback
            if volume > 50:
                console.print(f"[dim]  volume: {volume}[/dim]", end="\r")

            if volume > SILENCE_THRESHOLD:
                speaking = True
                silent_chunks = 0
            elif speaking:
                silent_chunks += 1
                if silent_chunks > int(RATE / CHUNK * SILENCE_DURATION):
                    break

            # No speech detected after 5 seconds — bail early
            if not speaking and i > int(RATE / CHUNK * 5):
                break

        stream.stop_stream()
        stream.close()
        p.terminate()
        return b"".join(frames) if speaking else b""

    async def _transcribe(self, audio_bytes: bytes) -> str:
        try:
            return await asyncio.to_thread(self._transcribe_sync, audio_bytes)
        except ImportError:
            logger.warning("faster-whisper not installed")
            return ""

    def _transcribe_sync(self, audio_bytes: bytes) -> str:
        from faster_whisper import WhisperModel  # type: ignore

        if self._whisper_model is None:
            self._whisper_model = WhisperModel("base.en", device="cpu", compute_type="int8")

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            with wave.open(f.name, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(16000)
                wf.writeframes(audio_bytes)
            temp_path = f.name

        try:
            segments, _ = self._whisper_model.transcribe(temp_path, beam_size=1)
            return " ".join(s.text for s in segments).strip()
        finally:
            os.unlink(temp_path)

    async def _speak(self, text: str) -> None:
        spoken = self._compress_for_speech(text)
        if not spoken:
            return
        try:
            from elevenlabs.client import ElevenLabs  # type: ignore
            from elevenlabs import play  # type: ignore
            client = ElevenLabs(api_key=self.config.elevenlabs_api_key)
            audio = client.text_to_speech.convert(
                text=spoken,
                voice_id="pNInz6obpgDQGcFmaJgB",
                model_id="eleven_turbo_v2",
            )
            await asyncio.to_thread(play, audio)
        except Exception:
            await asyncio.create_subprocess_exec("say", "-r", "210", spoken)

    @staticmethod
    def _compress_for_speech(text: str) -> str:
        text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
        text = re.sub(r"`(.+?)`", r"\1", text)
        text = re.sub(r"#{1,6}\s", "", text)
        text = re.sub(r"\[(.+?)\]\(.+?\)", r"\1", text)
        text = re.sub(r"\n+", " ", text)
        if len(text) > 300:
            text = text[:297] + "..."
        return text.strip()
