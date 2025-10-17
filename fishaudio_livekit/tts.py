import asyncio
import contextlib
import os
import uuid
from dataclasses import dataclass

import httpx
import ormsgpack
from fish_audio_sdk import Prosody, TTSRequest, WebSocketSession
from httpx_ws import (
    WebSocketDisconnect,
    WebSocketNetworkError,
    WebSocketUpgradeError,
    aconnect_ws,
)
from wsproto.utilities import LocalProtocolError

from livekit.agents import (
    DEFAULT_API_CONNECT_OPTIONS,
    APIConnectionError,
    APIConnectOptions,
    tts,
)

FISHAUDIO_API_KEY = os.getenv("FISHAUDIO_API_KEY")
SAMPLE_RATE = 44100
NUM_CHANNELS = 1
WAV_MIME_TYPE = "audio/wav"
PCM_MIME_TYPE = "audio/pcm"


@dataclass
class _TTSOptions:
    language: str
    reference_id: str | None = None
    temperature: float = 0.7
    top_p: float = 0.7
    chunk_length: int | None = 120
    latency: str | None = None
    model: str = "s1"
    speed: float = 1.0
    volume: float = 0.0
    sample_rate: int = 44100


class TTS(tts.TTS):
    def __init__(
        self,
        *,
        api_key: str | None = None,
        language: str = "en",
        reference_id: str = None,
        temperature: float = 0.7,
        top_p: float = 0.7,
        chunk_length: int | None = 120,
        latency: str | None = None,
        model: str = "s1",
        speed: float = 1.0,
        volume: float = 0.0,
        sample_rate: int = 44100,
    ):
        super().__init__(
            capabilities=tts.TTSCapabilities(streaming=True),
            sample_rate=sample_rate,
            num_channels=NUM_CHANNELS,
        )
        self._opts = _TTSOptions(
            language=language,
            reference_id=reference_id,
            temperature=temperature,
            top_p=top_p,
            chunk_length=chunk_length,
            latency=latency,
            model=model,
            speed=speed,
            volume=volume,
            sample_rate=sample_rate,
        )
        self._api_key = api_key or FISHAUDIO_API_KEY
        if not self._api_key:
            raise APIConnectionError("FISHAUDIO_API_KEY not set")
        self._ws = WebSocketSession(self._api_key)

    def synthesize(
        self,
        text: str,
        *,
        conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS,
    ) -> "ChunkedStream":
        return ChunkedStream(
            tts=self,
            input_text=text,
            conn_options=conn_options,
            opts=self._opts,
            ws=self._ws,
        )

    def stream(
        self,
        *,
        conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS,
    ) -> "Stream":
        return Stream(
            tts=self,
            conn_options=conn_options,
            opts=self._opts,
            api_key=self._api_key,
        )


class ChunkedStream(tts.ChunkedStream):
    def __init__(
        self,
        *,
        tts: TTS,
        input_text: str,
        conn_options: APIConnectOptions,
        opts: _TTSOptions,
        ws: WebSocketSession,
    ) -> None:
        super().__init__(tts=tts, input_text=input_text, conn_options=conn_options)
        self._ws = ws
        self._opts = opts

    async def _run(self, output_emitter: tts.AudioEmitter) -> None:
        request_id = str(uuid.uuid4().hex)[:12]
        try:
            request_kwargs = {
                "text": self.input_text,
                "reference_id": self._opts.reference_id,
                "format": "pcm",
                "temperature": self._opts.temperature,
                "top_p": self._opts.top_p,
                "sample_rate": self._opts.sample_rate,
                "prosody": Prosody(speed=self._opts.speed, volume=self._opts.volume),
            }
            if self._opts.chunk_length is not None:
                request_kwargs["chunk_length"] = self._opts.chunk_length
            if self._opts.latency is not None:
                request_kwargs["latency"] = self._opts.latency

            tts_request = TTSRequest(**request_kwargs)

            loop = asyncio.get_running_loop()
            courier_queue: asyncio.Queue = asyncio.Queue()
            sentinel = object()

            # Bridge the blocking Fish Audio generator into the async emitter.
            def run_tts() -> None:
                try:
                    for chunk in self._ws.tts(tts_request, [], backend=self._opts.model):
                        loop.call_soon_threadsafe(courier_queue.put_nowait, chunk)
                except Exception as exc:  # pragma: no cover - network runtime
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
                    output_emitter.push(item)

            await worker
            if error is not None:
                raise error
            output_emitter.end_input()
        except Exception as e:
            raise APIConnectionError() from e


class Stream(tts.SynthesizeStream):
    API_BASE_URL = "https://api.fish.audio"

    def __init__(
        self,
        *,
        tts: TTS,
        conn_options: APIConnectOptions,
        opts: _TTSOptions,
        api_key: str,
    ) -> None:
        super().__init__(tts=tts, conn_options=conn_options)
        self._opts = opts
        self._api_key = api_key

    async def _run(self, output_emitter: tts.AudioEmitter) -> None:
        request_id = str(uuid.uuid4().hex)[:12]
        output_emitter.initialize(
            request_id=request_id,
            sample_rate=self._opts.sample_rate,
            num_channels=NUM_CHANNELS,
            mime_type=PCM_MIME_TYPE,
            stream=True,
        )
        # Delay start_segment until first audio chunk arrives to prevent voice breaking

        request_kwargs = {
            "text": "",
            "format": "pcm",
            "temperature": self._opts.temperature,
            "top_p": self._opts.top_p,
            "sample_rate": self._opts.sample_rate,
            "prosody": Prosody(speed=self._opts.speed, volume=self._opts.volume),
        }
        if self._opts.reference_id is not None:
            request_kwargs["reference_id"] = self._opts.reference_id
        if self._opts.chunk_length is not None:
            request_kwargs["chunk_length"] = self._opts.chunk_length
        if self._opts.latency is not None:
            request_kwargs["latency"] = self._opts.latency

        tts_request = TTSRequest(**request_kwargs).model_dump(exclude_none=True)

        timeout = self._conn_options.timeout
        client = httpx.AsyncClient(
            base_url=self.API_BASE_URL,
            headers={"Authorization": f"Bearer {self._api_key}"},
            timeout=timeout,
        )

        async def _send_loop(ws) -> None:
            pending: list[str] = []
            started = False
            last_sent = ""

            async def _flush_pending(force: bool = False) -> None:
                nonlocal started, last_sent
                if not pending:
                    return
                text_raw = "".join(pending)
                pending.clear()
                normalized = text_raw.strip()
                if not normalized:
                    return
                # REMOVED: Text deduplication check that could skip legitimate repeated text
                # This was causing issues where repeated text wouldn't be synthesized
                # Original line: if not force and normalized == last_sent: return
                last_sent = normalized
                if not started:
                    self._mark_started()
                    started = True
                await ws.send_bytes(
                    ormsgpack.packb({"event": "text", "text": text_raw})
                )
                await ws.send_bytes(ormsgpack.packb({"event": "flush"}))

            try:
                async for item in self._input_ch:
                    if isinstance(item, self._FlushSentinel):
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
            segment_started = False
            # Use timeout from conn_options, default to 30 seconds like Cartesia
            receive_timeout = timeout if timeout else 30.0
            try:
                while True:
                    # Add timeout to prevent indefinite hanging
                    message = await asyncio.wait_for(
                        ws.receive_bytes(),
                        timeout=receive_timeout
                    )
                    data = ormsgpack.unpackb(message)
                    event = data.get("event")
                    if event == "audio":
                        chunk = data.get("audio")
                        if chunk:
                            # Start segment only when first audio chunk arrives
                            if not segment_started:
                                output_emitter.start_segment(segment_id=request_id)
                                segment_started = True
                            output_emitter.push(chunk)
                    elif event == "finish":
                        if data.get("reason") == "error":
                            raise APIConnectionError()
                        break
            except asyncio.TimeoutError:
                # No message received within timeout - assume synthesis complete
                # This prevents hanging when finish event doesn't arrive
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
