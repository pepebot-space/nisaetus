"""
Pepebot Live API client integrated with HeyCyan Smart Glasses.

Flow:
1. Connect to glasses via BLE
2. Enable WiFi transfer → glasses becomes hotspot
3. Capture photos from glasses camera, send as video frames to Live API
4. Stream local mic audio to Live API (glasses mic stores OPUS files)
5. Play AI audio responses through glasses speaker via AI Speak mode
6. Receive AI text/audio from websocket
"""

import asyncio
import base64
import json
import logging
import signal
import sys
from typing import Optional

import audioop
import pyaudio
import websockets

from .glasses import HeyCyanGlasses
from .protocol import AISpeakMode, DeviceMode

logger = logging.getLogger(__name__)

# ── Audio Config ────────────────────────────────────────────────────────────
INPUT_RATE = 16000
OUTPUT_RATE = 24000
CHANNELS = 1
SAMPLE_WIDTH = 2
FORMAT = pyaudio.paInt16
INPUT_CHUNK = 2048
OUTPUT_CHUNK = 4096
OUTPUT_PREBUFFER_CHUNKS = 3

# ── Noise Gate ──────────────────────────────────────────────────────────────
ENABLE_NOISE_GATE = True
NOISE_FLOOR_ALPHA = 0.95
NOISE_GATE_MULTIPLIER = 2.0
NOISE_GATE_MIN_RMS = 180
NOISE_GATE_HANGOVER = 3

# ── Video (glasses camera) ──────────────────────────────────────────────────
VIDEO_MIME = "image/jpeg"
VIDEO_INTERVAL_SEC = 2.0  # capture every 2s (glasses camera is slower than webcam)

# ── Pepebot ─────────────────────────────────────────────────────────────────
DEFAULT_URL = "ws://localhost:18790/v1/live"
ENABLE_BARGE_IN = False
BOT_SPEAKING_HOLD_SEC = 0.8


class NoiseGate:
    def __init__(self):
        self.noise_floor = float(NOISE_GATE_MIN_RMS)
        self.hangover_left = 0

    def process(self, pcm_bytes: bytes) -> bytes:
        if not pcm_bytes:
            return pcm_bytes
        rms = audioop.rms(pcm_bytes, SAMPLE_WIDTH)
        if rms < self.noise_floor * 1.5:
            self.noise_floor = NOISE_FLOOR_ALPHA * self.noise_floor + (1 - NOISE_FLOOR_ALPHA) * rms
        threshold = max(NOISE_GATE_MIN_RMS, self.noise_floor * NOISE_GATE_MULTIPLIER)
        if rms >= threshold:
            self.hangover_left = NOISE_GATE_HANGOVER
            return pcm_bytes
        if self.hangover_left > 0:
            self.hangover_left -= 1
            return pcm_bytes
        return b"\x00" * len(pcm_bytes)


def try_parse_json(data):
    try:
        if isinstance(data, bytes):
            return json.loads(data.decode("utf-8", errors="ignore"))
        return json.loads(data)
    except Exception:
        return None


def extract_inline_audio(parsed: dict) -> Optional[bytes]:
    server_content = parsed.get("serverContent")
    if not isinstance(server_content, dict):
        return None
    model_turn = server_content.get("modelTurn")
    if not isinstance(model_turn, dict):
        return None
    parts = model_turn.get("parts")
    if not isinstance(parts, list):
        return None

    chunks = []
    for part in parts:
        if not isinstance(part, dict):
            continue
        inline_data = part.get("inlineData")
        if not isinstance(inline_data, dict):
            continue
        b64_audio = inline_data.get("data")
        if not isinstance(b64_audio, str) or not b64_audio:
            continue
        normalized = b64_audio.replace("-", "+").replace("_", "/")
        while len(normalized) % 4 != 0:
            normalized += "="
        try:
            chunks.append(base64.b64decode(normalized))
        except Exception:
            continue

    return b"".join(chunks) if chunks else None


class NisaetusLive:
    """
    Main live session: glasses BLE + pepebot websocket.

    Modes:
    - "glasses": Use glasses camera (WiFi) + local mic + glasses speaker
    - "local":   Use local webcam + local mic + local speaker (fallback)
    - "hybrid":  Use glasses camera (WiFi) + local mic + local speaker
    """

    def __init__(self, url: str = DEFAULT_URL, mode: str = "glasses"):
        self.url = url
        self.mode = mode
        self.glasses = HeyCyanGlasses()
        self.stop_event = asyncio.Event()

    async def run(self, glasses_address: Optional[str] = None):
        """Main entry point."""
        loop = asyncio.get_running_loop()

        def _stop(*_):
            self.stop_event.set()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, _stop)
            except NotImplementedError:
                pass
        signal.signal(signal.SIGINT, lambda *_: self.stop_event.set())

        # Step 1: Connect to glasses
        if self.mode in ("glasses", "hybrid"):
            await self._connect_glasses(glasses_address)

        # Step 2: Connect to pepebot live API
        await self._run_live_session(loop)

        # Cleanup
        if self.glasses.connected:
            await self.glasses.disconnect()

    async def _connect_glasses(self, address: Optional[str] = None):
        """Scan, connect to glasses via BLE."""
        if address:
            await self.glasses.connect(address)
        else:
            devices = await self.glasses.scan(timeout=10.0)
            if not devices:
                logger.warning("No glasses found, falling back to local mode")
                self.mode = "local"
                return
            print(f"\nFound {len(devices)} device(s):")
            for i, (dev, adv) in enumerate(devices):
                print(f"  [{i}] {dev.name or 'Unknown'} ({dev.address}) RSSI: {adv.rssi}")
            if len(devices) > 1:
                idx = int(input("Select device [0]: ") or "0")
            else:
                idx = 0
            await self.glasses.connect(devices[idx][0].address)

        await self.glasses.get_battery()
        await self.glasses.sync_time()
        logger.info("Glasses battery: %d%%", self.glasses.battery_level)

    async def _run_live_session(self, loop):
        """WebSocket session with pepebot."""
        p = pyaudio.PyAudio()
        output_queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=256)
        bot_speaking_until = 0.0
        noise_gate = NoiseGate()

        stream_out = None
        stream_in = None
        try:
            stream_out = p.open(format=FORMAT, channels=CHANNELS, rate=OUTPUT_RATE,
                                output=True, frames_per_buffer=OUTPUT_CHUNK)
        except OSError as e:
            logger.warning("Cannot open audio output: %s", e)

        try:
            stream_in = p.open(format=FORMAT, channels=CHANNELS, rate=INPUT_RATE,
                               input=True, frames_per_buffer=INPUT_CHUNK)
        except OSError as e:
            logger.warning("Cannot open audio input: %s (check microphone permission)", e)
            print("WARNING: No microphone available. Grant mic permission or check audio devices.")

        async def enqueue_audio(pcm: bytes):
            nonlocal bot_speaking_until
            if not pcm or len(pcm) % 2 != 0:
                return
            try:
                await asyncio.wait_for(output_queue.put(pcm), timeout=0.5)
                bot_speaking_until = max(bot_speaking_until, loop.time() + BOT_SPEAKING_HOLD_SEC)
            except asyncio.TimeoutError:
                pass

        async def playback_worker():
            """Play AI audio responses through local speaker."""
            if not stream_out:
                logger.info("No speaker, playback disabled")
                return
            bytes_per_chunk = OUTPUT_CHUNK * SAMPLE_WIDTH
            prebuffer_target = OUTPUT_PREBUFFER_CHUNKS * bytes_per_chunk
            pending = bytearray()
            started = False

            while not self.stop_event.is_set():
                try:
                    pcm = await asyncio.wait_for(output_queue.get(), timeout=0.02)
                    pending.extend(pcm)
                except asyncio.TimeoutError:
                    pass

                if not started:
                    if len(pending) < prebuffer_target:
                        continue
                    started = True

                if len(pending) >= bytes_per_chunk:
                    frame = bytes(pending[:bytes_per_chunk])
                    del pending[:bytes_per_chunk]
                else:
                    frame = bytes(pending) + b"\x00" * (bytes_per_chunk - len(pending))
                    pending.clear()

                try:
                    await asyncio.to_thread(stream_out.write, frame)
                except Exception as e:
                    if not self.stop_event.is_set():
                        logger.error("Playback error: %s", e)
                    return

            # Notify glasses speaker when bot is speaking
            if self.mode == "glasses" and self.glasses.connected:
                try:
                    await self.glasses.ai_speak(AISpeakMode.START)
                except Exception:
                    pass

        async def sender_audio(ws):
            """Stream local mic audio to pepebot."""
            if not stream_in:
                logger.info("No mic, audio sender disabled")
                return
            while not self.stop_event.is_set():
                try:
                    if not ENABLE_BARGE_IN and loop.time() < bot_speaking_until:
                        await asyncio.sleep(0.02)
                        continue
                    data = await asyncio.to_thread(stream_in.read, INPUT_CHUNK,
                                                   exception_on_overflow=False)
                    if ENABLE_NOISE_GATE:
                        data = noise_gate.process(data)
                    b64 = base64.b64encode(data).decode("utf-8")
                    await ws.send(json.dumps({
                        "realtimeInput": {
                            "mediaChunks": [{
                                "mimeType": "audio/pcm;rate=16000",
                                "data": b64,
                            }]
                        }
                    }))
                except asyncio.CancelledError:
                    return
                except Exception as e:
                    if not self.stop_event.is_set():
                        logger.error("Audio sender error: %s", e)
                    self.stop_event.set()
                    return

        async def sender_video(ws, video_enabled: bool):
            """Capture AI photo thumbnails from glasses via BLE and send to pepebot."""
            if not video_enabled:
                return
            if not self.glasses.connected:
                logger.info("Glasses not connected, video sender disabled")
                return

            logger.info("Glasses camera sender active (AI photo thumbnails via BLE)")
            while not self.stop_event.is_set():
                try:
                    # Capture AI photo and get thumbnail via BLE
                    jpeg_data = await self.glasses.capture_and_get_thumbnail(
                        thumbnail_size=0x02, timeout=8.0
                    )
                    if not jpeg_data:
                        await asyncio.sleep(VIDEO_INTERVAL_SEC)
                        continue

                    b64 = base64.b64encode(jpeg_data).decode("utf-8")
                    await ws.send(json.dumps({
                        "realtimeInput": {
                            "mediaChunks": [{
                                "mimeType": VIDEO_MIME,
                                "data": b64,
                            }]
                        }
                    }))
                    logger.debug("Sent glasses thumbnail (%d bytes)", len(jpeg_data))

                except asyncio.CancelledError:
                    return
                except Exception as e:
                    if not self.stop_event.is_set():
                        logger.debug("Video sender: %s", e)

                await asyncio.sleep(VIDEO_INTERVAL_SEC)

        async def receiver(ws):
            """Receive AI responses from pepebot."""
            while not self.stop_event.is_set():
                try:
                    message = await ws.recv()
                except asyncio.CancelledError:
                    return
                except websockets.exceptions.ConnectionClosed as e:
                    if not self.stop_event.is_set():
                        logger.error("Connection closed: %s", e)
                    self.stop_event.set()
                    return
                except Exception as e:
                    if not self.stop_event.is_set():
                        logger.error("Receiver error: %s", e)
                    self.stop_event.set()
                    return

                parsed = try_parse_json(message)
                if parsed is None:
                    continue

                if parsed.get("error"):
                    logger.error("Server error: %s", parsed["error"])
                    continue

                # Extract and play audio
                audio = extract_inline_audio(parsed)
                if audio and len(audio) >= 2 and len(audio) % 2 == 0:
                    await enqueue_audio(audio)

                # Print text responses
                model_turn = parsed.get("serverContent", {}).get("modelTurn", {})
                parts = model_turn.get("parts", []) if isinstance(model_turn, dict) else []
                for part in parts:
                    if isinstance(part, dict) and part.get("text"):
                        print(f"AI: {part['text']}")

        # ── WebSocket Connection ────────────────────────────────────────
        try:
            print(f"\nConnecting to Pepebot at {self.url}...")
            async with websockets.connect(
                self.url, max_size=20 * 1024 * 1024,
                ping_interval=20, ping_timeout=20, close_timeout=5,
            ) as ws:
                # Setup
                setup_msg = {
                    "setup": {
                        "provider": "vertex",
                        "model": "gemini-live-2.5-flash-native-audio",
                        "agent": "default",
                        "enable_tools": True,
                    }
                }
                await ws.send(json.dumps(setup_msg))

                video_enabled = False
                setup_ok = False
                while not setup_ok and not self.stop_event.is_set():
                    msg = await asyncio.wait_for(ws.recv(), timeout=15)
                    parsed = try_parse_json(msg)
                    if parsed is None:
                        continue
                    if parsed.get("error"):
                        logger.error("Setup error: %s", parsed["error"])
                        return
                    if parsed.get("status") == "connected":
                        video_meta = parsed.get("video", {})
                        video_enabled = bool(video_meta.get("enabled"))
                        print(f"Proxy: {parsed.get('provider')} -> {parsed.get('model')}")
                        print(f"Video: requested={video_meta.get('requested')} "
                              f"supported={video_meta.get('supported')} "
                              f"enabled={video_meta.get('enabled')}")
                        continue
                    if "setupComplete" in parsed:
                        setup_ok = True

                if not setup_ok:
                    return

                mode_label = {
                    "glasses": "Glasses camera (BLE) + Local mic + Local speaker",
                    "hybrid": "Glasses camera (BLE) + Local mic + Local speaker",
                    "local": "Local mic + Local speaker (no glasses)",
                }
                print(f"\nMode: {mode_label.get(self.mode, self.mode)}")
                print(f"Mic: {INPUT_RATE}Hz | Speaker: {OUTPUT_RATE}Hz")
                if self.glasses.connected:
                    print(f"Glasses: connected (battery {self.glasses.battery_level}%)")
                if self.glasses.connected and video_enabled:
                    print(f"Camera: AI photo thumbnails via BLE (every {VIDEO_INTERVAL_SEC}s)")
                print("Speak now... Press Ctrl+C to stop.\n")

                tasks = [
                    asyncio.create_task(playback_worker()),
                    asyncio.create_task(sender_audio(ws)),
                    asyncio.create_task(sender_video(ws, video_enabled)),
                    asyncio.create_task(receiver(ws)),
                ]

                try:
                    await self.stop_event.wait()
                except KeyboardInterrupt:
                    self.stop_event.set()

                for t in tasks:
                    t.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)

        except ConnectionRefusedError:
            print(f"Cannot connect to {self.url}. Ensure pepebot gateway is running.")
        except asyncio.TimeoutError:
            print("Timeout waiting for setupComplete")
        finally:
            if stream_in:
                try:
                    stream_in.stop_stream()
                    stream_in.close()
                except Exception:
                    pass
            if stream_out:
                try:
                    stream_out.stop_stream()
                    stream_out.close()
                except Exception:
                    pass
            p.terminate()

