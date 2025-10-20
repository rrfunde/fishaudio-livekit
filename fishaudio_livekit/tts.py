"""
Stable Fish Audio TTS adapter for LiveKit agents.

This module wraps the upstream ``fishaudio_livekit`` implementation and adds a
deterministic fade-in smoothing step for the first audio frames of every
generation. The native websocket client
(`fishaudio_tts_websocket.py`) yields clean audio because playback buffers the
initial samples; when streaming directly through LiveKit we explicitly soften
the first ~200 ms to avoid the audible instability reported at the start of
each user turn.
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from array import array
from typing import Optional

import httpx
import ormsgpack
from fish_audio_sdk import Prosody, TTSRequest
from httpx_ws import (
    WebSocketDisconnect,
    WebSocketNetworkError,
    WebSocketUpgradeError,
    aconnect_ws,
)
from wsproto.utilities import LocalProtocolError

from livekit.agents import (
    APIConnectionError,
    APIConnectOptions,
    DEFAULT_API_CONNECT_OPTIONS,
    tts as lk_tts,
)

from fishaudio_livekit.tts import ChunkedStream as FishChunkedStream  # type: ignore
from fishaudio_livekit.tts import Stream as FishStream  # type: ignore

PCM_MIME_TYPE = "audio/pcm"
NUM_CHANNELS = 1
DEFAULT_FADE_MS = 220


class _FadeInProcessor:
    """Gradually ramps in the first few PCM frames to avoid startup pops."""

    def __init__(
        self,
        *,
        sample_rate: int,
        num_channels: int,
        duration_ms: int = DEFAULT_FADE_MS,
    ) -> None:
        self._sample_width = 2  # Fish Audio returns pcm_s16le
        self._num_channels = max(1, num_channels)
        frame_width = self._sample_width * self._num_channels
        if frame_width <= 0:
            raise ValueError("invalid frame width for fade-in processor")
        self._frame_width = frame_width
        frames = max(0, int(sample_rate * duration_ms / 1000))
        self._fade_frames = frames
        self._processed_frames = 0

    def process(self, chunk: bytes) -> bytes:
        if not chunk or self._fade_frames <= 0:
            return chunk
        if len(chunk) % self._frame_width != 0:
            # Unexpected frame alignment; skip modifying to avoid corruption.
            return chunk

        frame_count = len(chunk) // self._frame_width
        if self._processed_frames >= self._fade_frames or frame_count == 0:
            self._processed_frames += frame_count
            return chunk

        fade_remaining = self._fade_frames - self._processed_frames
        apply_frames = min(frame_count, fade_remaining)
        if apply_frames <= 0:
            self._processed_frames += frame_count
            return chunk

        samples = array("h")
        samples.frombytes(chunk)
        start_frame = self._processed_frames
        for frame_idx in range(apply_frames):
            fade_position = start_frame + frame_idx
            factor = fade_position / self._fade_frames
            if factor > 1.0:
                factor = 1.0
            base_index = frame_idx * self._num_channels
            for channel in range(self._num_channels):
                idx = base_index + channel
                samples[idx] = int(samples[idx] * factor)

        self._processed_frames += frame_count
        return samples.tobytes()


class _FadingChunkedStream(FishChunkedStream):
    """ChunkedStream that applies fade-in smoothing before emitting audio."""

    def __init__(
        self,
        *,
        tts: "TTS",
        input_text: str,
        conn_options: APIConnectOptions,
        fade_duration_ms: Optional[int],
    ) -> None:
        super().__init__(
            tts=tts,
            input_text=input_text,
            conn_options=conn_options,
            opts=tts._opts,  # noqa: SLF001
            ws=tts._ws,  # noqa: SLF001
        )
        self._fade_duration_ms = fade_duration_ms

    async def _run(self, output_emitter: lk_tts.AudioEmitter) -> None:  # noqa: D401
        request_id = str(uuid.uuid4().hex)[:12]
        fade_processor = (
            _FadeInProcessor(
                sample_rate=self._opts.sample_rate,
                num_channels=NUM_CHANNELS,
                duration_ms=self._fade_duration_ms,
            )
            if self._fade_duration_ms and self._fade_duration_ms > 0
            else None
        )
        try:
            request_kwargs = {
                "text": self.input_text,
                "reference_id": self._opts.reference_id,
                "format": "pcm",
                "temperature": self._opts.temperature,
                "top_p": self._opts.top_p,
                "sample_rate": self._opts.sample_rate,
                "prosody": Prosody(
                    speed=self._opts.speed,
                    volume=self._opts.volume,
                ),
            }
            if self._opts.chunk_length is not None:
                request_kwargs["chunk_length"] = self._opts.chunk_length
            if self._opts.latency is not None:
                request_kwargs["latency"] = self._opts.latency

            tts_request = TTSRequest(**request_kwargs)

            loop = asyncio.get_running_loop()
            courier_queue: asyncio.Queue = asyncio.Queue()
            sentinel = object()

            def run_tts() -> None:
                try:
                    for chunk in self._ws.tts(tts_request, [], backend=self._opts.model):
                        loop.call_soon_threadsafe(courier_queue.put_nowait, chunk)
                except Exception as exc:  # pragma: no cover
                    loop.call_soon_threadsafe(courier_queue.put_nowait, exc)
                finally:
                    loop.call_soon_threadsafe(courier_queue.put_nowait, sentinel)

            worker = loop.run_in_executor(None, run_tts)

            output_emitter.initialize(
                request_id=request_id,
                sample_rate=self._opts.sample_rate,
                num_channels=NUM_CHANNELS,
                mime_type=PCM_MIME_TYPE,
            )
            error: Exception | None = None
            while True:
                item = await courier_queue.get()
                if item is sentinel:
                    break
                if isinstance(item, Exception):
                    error = item
                    continue
                if error is None:
                    processed = (
                        fade_processor.process(item) if fade_processor else item
                    )
                    output_emitter.push(processed)

            await worker
            if error is not None:
                raise error
            output_emitter.end_input()
        except Exception as exc:  # pragma: no cover
            raise APIConnectionError() from exc


class _FadingStream(FishStream):
    """Streaming synthesize implementation with fade-in smoothing."""

    def __init__(
        self,
        *,
        tts: "TTS",
        conn_options: APIConnectOptions,
        fade_duration_ms: Optional[int],
    ) -> None:
        super().__init__(
            tts=tts,
            conn_options=conn_options,
            opts=tts._opts,  # noqa: SLF001
            api_key=tts._api_key,  # noqa: SLF001
        )
        self._fade_duration_ms = fade_duration_ms

    async def _run(self, output_emitter: lk_tts.AudioEmitter) -> None:  # noqa: D401
        request_id = str(uuid.uuid4().hex)[:12]
        fade_processor = (
            _FadeInProcessor(
                sample_rate=self._opts.sample_rate,
                num_channels=NUM_CHANNELS,
                duration_ms=self._fade_duration_ms,
            )
            if self._fade_duration_ms and self._fade_duration_ms > 0
            else None
        )

        output_emitter.initialize(
            request_id=request_id,
            sample_rate=self._opts.sample_rate,
            num_channels=NUM_CHANNELS,
            mime_type=PCM_MIME_TYPE,
            stream=True,
        )

        request_kwargs = {
            "text": "",
            "format": "pcm",
            "temperature": self._opts.temperature,
            "top_p": self._opts.top_p,
            "sample_rate": self._opts.sample_rate,
            "prosody": Prosody(
                speed=self._opts.speed,
                volume=self._opts.volume,
            ),
        }
        if self._opts.reference_id is not None:
            request_kwargs["reference_id"] = self._opts.reference_id
        if self._opts.chunk_length is not None:
            request_kwargs["chunk_length"] = self._opts.chunk_length
        if self._opts.latency is not None:
            request_kwargs["latency"] = self._opts.latency

        tts_request = TTSRequest(**request_kwargs).model_dump(exclude_none=True)

        timeout = self._conn_options.timeout  # noqa: SLF001
        client = httpx.AsyncClient(
            base_url=self.API_BASE_URL,
            headers={"Authorization": f"Bearer {self._api_key}"},
            timeout=timeout,
        )

        async def _send_loop(ws) -> None:
            pending: list[str] = []
            started = False

            async def _flush_pending(force: bool = False) -> None:
                nonlocal started
                if not pending:
                    return
                text_raw = "".join(pending)
                pending.clear()
                normalized = text_raw.strip()
                if not normalized:
                    return
                if not started:
                    self._mark_started()
                    started = True
                await ws.send_bytes(
                    ormsgpack.packb({"event": "text", "text": text_raw})
                )
                await ws.send_bytes(ormsgpack.packb({"event": "flush"}))

            try:
                async for item in self._input_ch:  # noqa: SLF001
                    if isinstance(item, self._FlushSentinel):  # noqa: SLF001
                        await _flush_pending()
                        continue
                    pending.append(item)

                await _flush_pending(force=True)

                if started:
                    try:
                        await ws.send_bytes(ormsgpack.packb({"event": "stop"}))
                    except LocalProtocolError:
                        pass
            except Exception:
                raise

        async def _recv_loop(ws) -> None:
            receive_timeout = timeout if timeout else 30.0
            try:
                while True:
                    message = await asyncio.wait_for(
                        ws.receive_bytes(),
                        timeout=receive_timeout,
                    )
                    data = ormsgpack.unpackb(message)
                    event = data.get("event")
                    if event == "audio":
                        chunk = data.get("audio")
                        if chunk:
                            processed = (
                                fade_processor.process(chunk)
                                if fade_processor
                                else chunk
                            )
                            output_emitter.push(processed)
                    elif event == "finish":
                        if data.get("reason") == "error":
                            raise APIConnectionError()
                        break
            except asyncio.TimeoutError:
                pass
            except WebSocketDisconnect as exc:
                raise APIConnectionError() from exc

        try:
            async with client:
                async with aconnect_ws(
                    "/v1/tts/live",
                    client=client,
                    headers={"model": self._opts.model},
                ) as ws:
                    await ws.send_bytes(
                        ormsgpack.packb({"event": "start", "request": tts_request})
                    )

                    output_emitter.start_segment(segment_id=request_id)

                    send_task = asyncio.create_task(_send_loop(ws))
                    recv_task = asyncio.create_task(_recv_loop(ws))

                    try:
                        await asyncio.gather(send_task, recv_task)
                    finally:
                        for task in (send_task, recv_task):
                            if not task.done():
                                task.cancel()
                                with contextlib.suppress(asyncio.CancelledError):
                                    await task
        except (
            httpx.HTTPError,
            asyncio.TimeoutError,
            WebSocketNetworkError,
            WebSocketUpgradeError,
            LocalProtocolError,
        ) as exc:
            raise APIConnectionError() from exc
        finally:
            output_emitter.end_input()


class TTS(FishTTS):
    """
    Drop-in replacement for ``fishaudio_livekit.TTS`` that smooths the first
    audio frames via a configurable fade-in.
    """

    def __init__(
        self,
        *,
        fade_duration_ms: Optional[int] = DEFAULT_FADE_MS,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self._fade_duration_ms = fade_duration_ms

    def synthesize(
        self,
        text: str,
        *,
        conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS,
    ) -> lk_tts.ChunkedStream:
        return _FadingChunkedStream(
            tts=self,
            input_text=text,
            conn_options=conn_options,
            fade_duration_ms=self._fade_duration_ms,
        )

    def stream(
        self,
        *,
        conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS,
    ) -> lk_tts.SynthesizeStream:
        return _FadingStream(
            tts=self,
            conn_options=conn_options,
            fade_duration_ms=self._fade_duration_ms,
        )
