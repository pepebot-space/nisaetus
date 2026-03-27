"""
HeyCyan Smart Glasses BLE Client
Handles scanning, connecting, sending commands, and receiving notifications.
"""

import asyncio
import logging
from typing import Optional, Callable

from bleak import BleakClient, BleakScanner
from bleak.backends.characteristic import BleakGATTCharacteristic

from .protocol import (
    ALL_KNOWN_SERVICES, DEVICE_NAME_PATTERNS,
    CHAR_EXTRA_WRITE, CHAR_EXTRA_NOTIFY,
    CHAR_QCSDK_WRITE, CHAR_QCSDK_NOTIFY,
    CHAR_CMD_NOTIFY, CHAR_DATA_NOTIFY,
    DeviceMode, AISpeakMode, VolumeMode, NotifyEvent,
    CmdId, MAGIC_BYTE, MAX_CHUNK_SIZE,
    build_packet, parse_packet, build_small_packet,
    cmd_set_mode, cmd_ai_photo, cmd_get_battery, cmd_get_version,
    cmd_get_media_count, cmd_set_volume, cmd_ai_speak,
)

logger = logging.getLogger(__name__)


class GlassesEvent:
    """Parsed notification event from glasses."""
    def __init__(self, event_type: int, raw: bytes):
        self.event_type = event_type
        self.raw = raw

    # Convenience accessors
    @property
    def battery_level(self) -> int:
        if self.event_type == NotifyEvent.BATTERY and len(self.raw) > 7:
            return self.raw[7]
        return -1

    @property
    def is_charging(self) -> bool:
        if self.event_type == NotifyEvent.BATTERY and len(self.raw) > 8:
            return bool(self.raw[8])
        return False

    @property
    def mic_active(self) -> bool:
        if self.event_type == NotifyEvent.MICROPHONE and len(self.raw) > 7:
            return self.raw[7] == 1
        return False


class HeyCyanGlasses:
    """BLE client for HeyCyan Smart Glasses."""

    def __init__(self):
        self.client: Optional[BleakClient] = None
        # Large data channel (de5bf72a/de5bf729) - main command channel
        self.serial_write_char = None
        self.serial_notify_char = None
        # Small data channel (6e400002/6e400003)
        self.small_write_char = None
        self.small_notify_char = None
        # Reassembly buffer for fragmented packets
        self._rx_buffer = bytearray()
        self._rx_expected_len = 0

        self._response_event = asyncio.Event()
        self._response_data: Optional[bytes] = None
        self._event_callbacks: list[Callable[[GlassesEvent], None]] = []

        # State
        self.battery_level = -1
        self.is_charging = False
        self.connected = False
        self.wifi_ssid: Optional[str] = None
        self.wifi_password: Optional[str] = None
        # Thumbnail received via BLE (JPEG bytes)
        self._thumbnail_event = asyncio.Event()
        self._thumbnail_data: Optional[bytes] = None
        # Audio stream from glasses mic (OPUS frames via cmd 0x59)
        self._audio_callback: Optional[Callable[[bytes], None]] = None

    # ── Event System ────────────────────────────────────────────────────

    def on_event(self, callback: Callable[[GlassesEvent], None]):
        self._event_callbacks.append(callback)

    def _emit_event(self, event: GlassesEvent):
        for cb in self._event_callbacks:
            try:
                cb(event)
            except Exception as e:
                logger.error("Event callback error: %s", e)

    # ── Scanning & Connection ───────────────────────────────────────────

    async def scan(self, timeout: float = 15.0) -> list:
        """Scan for HeyCyan/M02S glasses. Returns list of (device, adv_data)."""
        logger.info("Scanning for glasses (%.0fs)...", timeout)
        found = []
        seen_addresses = set()

        known_services_lower = set(s.lower() for s in ALL_KNOWN_SERVICES)

        def _on_detect(device, adv_data):
            if device.address in seen_addresses:
                return
            is_glasses = False

            # Check advertised service UUIDs
            if adv_data.service_uuids:
                for uuid in adv_data.service_uuids:
                    if uuid.lower() in known_services_lower:
                        is_glasses = True
                        break

            # Check service data keys
            if not is_glasses and adv_data.service_data:
                for uuid in adv_data.service_data:
                    if uuid.lower() in known_services_lower:
                        is_glasses = True
                        break

            # Check device name
            if not is_glasses:
                name = (device.name or adv_data.local_name or "").lower()
                if name and any(p in name for p in DEVICE_NAME_PATTERNS):
                    is_glasses = True

            if is_glasses:
                seen_addresses.add(device.address)
                found.append((device, adv_data))
                logger.info("Found: %s [%s] RSSI=%d", device.name, device.address, adv_data.rssi)

        scanner = BleakScanner(detection_callback=_on_detect)
        await scanner.start()
        await asyncio.sleep(timeout)
        await scanner.stop()
        return found

    async def connect(self, address: str):
        """Connect to glasses by BLE address."""
        logger.info("Connecting to %s...", address)
        self.client = BleakClient(address, disconnected_callback=self._on_disconnect)
        await self.client.connect()
        logger.info("Connected (MTU: %d)", self.client.mtu_size)

        await self._discover_characteristics()

        # Subscribe to serial port notifications (large data responses)
        if self.serial_notify_char:
            await self.client.start_notify(self.serial_notify_char, self._on_serial_notification)
            logger.info("Subscribed to serial notify")

        # Subscribe to small data notifications
        if self.small_notify_char:
            await self.client.start_notify(self.small_notify_char, self._on_small_notification)
            logger.info("Subscribed to small notify")

        self.connected = True
        await asyncio.sleep(0.5)

    async def _discover_characteristics(self):
        """Find serial port (large data) and small data characteristics."""
        all_chars = {}
        for service in self.client.services:
            logger.info("Service: %s (%s)", service.uuid, service.description)
            for char in service.characteristics:
                all_chars[char.uuid.lower()] = char
                logger.info("  Char: %s [%s]", char.uuid, ", ".join(char.properties))

        # Large data / serial port channel (de5bf72a write, de5bf729 notify)
        self.serial_write_char = all_chars.get(CHAR_EXTRA_WRITE.lower())
        self.serial_notify_char = all_chars.get(CHAR_EXTRA_NOTIFY.lower())

        # Small data channel (6e400002 write, 6e400003 notify)
        self.small_write_char = all_chars.get(CHAR_QCSDK_WRITE.lower())
        self.small_notify_char = all_chars.get(CHAR_QCSDK_NOTIFY.lower())

        if not self.serial_write_char:
            raise RuntimeError("Serial port write characteristic (de5bf72a) not found")

        logger.info("Serial write: %s", self.serial_write_char.uuid)
        logger.info("Serial notify: %s", self.serial_notify_char.uuid if self.serial_notify_char else "none")
        if self.small_write_char:
            logger.info("Small write: %s", self.small_write_char.uuid)
        if self.small_notify_char:
            logger.info("Small notify: %s", self.small_notify_char.uuid)

    async def disconnect(self):
        if self.client and self.client.is_connected:
            await self.client.disconnect()
        self.connected = False

    def _on_disconnect(self, client):
        self.connected = False
        logger.warning("Glasses disconnected")

    # ── BLE I/O ─────────────────────────────────────────────────────────

    async def send_command(self, cmd_id: int, payload: bytes,
                           wait_response: bool = False, timeout: float = 5.0) -> Optional[bytes]:
        """Send a framed large-data command via serial port (de5bf72a).

        Packet: [0xBC][cmd_id][len_lo][len_hi][crc16_lo][crc16_hi][payload]
        """
        if not self.client or not self.client.is_connected:
            raise RuntimeError("Not connected")

        packet = build_packet(cmd_id, payload)
        logger.debug("TX serial [cmd=0x%02X]: %s", cmd_id, packet.hex())

        # Fragment into MAX_CHUNK_SIZE chunks
        for i in range(0, len(packet), MAX_CHUNK_SIZE):
            chunk = packet[i:i + MAX_CHUNK_SIZE]
            await self.client.write_gatt_char(self.serial_write_char, chunk, response=False)

        if wait_response:
            self._response_event.clear()
            try:
                await asyncio.wait_for(self._response_event.wait(), timeout)
                return self._response_data
            except asyncio.TimeoutError:
                logger.warning("Response timeout for cmd 0x%02X", cmd_id)
                return None

    async def send_small(self, key: int, sub_data: bytes = b""):
        """Send a 16-byte small data packet via 6e400002."""
        if not self.small_write_char:
            logger.warning("Small data channel not available")
            return
        packet = build_small_packet(key, sub_data)
        logger.debug("TX small [key=0x%02X]: %s", key, packet.hex())
        await self.client.write_gatt_char(self.small_write_char, packet, response=False)

    def _on_serial_notification(self, char: BleakGATTCharacteristic, data: bytearray):
        """Handle large data notifications with packet reassembly."""
        logger.debug("RX serial [%s]: %s (len=%d)", char.uuid[:8], data.hex(), len(data))

        # Start of new packet
        if data[0] == MAGIC_BYTE and len(data) >= 6:
            payload_len = data[2] | (data[3] << 8)
            expected_total = payload_len + 6
            if len(data) >= expected_total:
                # Complete packet in one notification
                self._process_packet(bytes(data))
            else:
                # Need more fragments
                self._rx_buffer = bytearray(data)
                self._rx_expected_len = expected_total
        elif self._rx_buffer:
            # Continuation fragment
            self._rx_buffer.extend(data)
            if len(self._rx_buffer) >= self._rx_expected_len:
                self._process_packet(bytes(self._rx_buffer))
                self._rx_buffer = bytearray()
                self._rx_expected_len = 0

    def _process_packet(self, data: bytes):
        """Process a complete framed packet."""
        parsed = parse_packet(data)
        if not parsed:
            logger.warning("Failed to parse packet: %s", data.hex())
            return

        cmd_id, payload = parsed
        logger.info("RX packet cmd=0x%02X payload=%s", cmd_id, payload.hex())

        self._response_data = data
        self._response_event.set()

        # Parse glasses control response (cmd 0x41)
        if cmd_id == CmdId.GLASSES_CONTROL and len(payload) >= 2:
            data_type = payload[1] if len(payload) > 1 else 0
            logger.info("GlassesControl response: dataType=%d", data_type)

            # WiFi transfer response: payload contains SSID + password
            # Format: 02 01 04 01 [ssid_len_le16] [pass_len_le16] [ssid_bytes] [pass_bytes]
            if len(payload) >= 8 and payload[2] == DeviceMode.TRANSFER:
                try:
                    ssid_len = payload[4] | (payload[5] << 8)
                    pass_len = payload[6] | (payload[7] << 8)
                    offset = 8
                    if len(payload) >= offset + ssid_len + pass_len:
                        self.wifi_ssid = payload[offset:offset + ssid_len].decode("utf-8", errors="replace")
                        self.wifi_password = payload[offset + ssid_len:offset + ssid_len + pass_len].decode("utf-8", errors="replace")
                        logger.info("WiFi SSID: %s  Password: %s", self.wifi_ssid, self.wifi_password)
                except Exception as e:
                    logger.debug("Failed to parse WiFi info: %s", e)

        # Parse battery response (cmd 0x42)
        if cmd_id == CmdId.BATTERY and len(payload) >= 2:
            self.battery_level = payload[0]
            self.is_charging = bool(payload[1]) if len(payload) > 1 else False
            logger.info("Battery: %d%% %s", self.battery_level,
                        "(charging)" if self.is_charging else "")

        # Parse thumbnail response (cmd 0xFD)
        # Payload format: [header bytes...] [FFD8...JPEG data]
        if cmd_id == CmdId.PICTURE_THUMBNAILS and payload:
            # Find JPEG start marker (FF D8)
            jpeg_start = payload.find(b'\xff\xd8')
            if jpeg_start >= 0:
                self._thumbnail_data = bytes(payload[jpeg_start:])
                logger.info("Received thumbnail JPEG (%d bytes)", len(self._thumbnail_data))
            else:
                self._thumbnail_data = bytes(payload)
                logger.info("Received thumbnail raw (%d bytes)", len(payload))
            self._thumbnail_event.set()

        # Audio stream from mic (cmd 0x59 = GPT_UPLOAD)
        if cmd_id == CmdId.GPT_UPLOAD and payload and self._audio_callback:
            # Strip trailing zeros from 40-byte packet
            end = len(payload)
            while end > 0 and payload[end - 1] == 0:
                end -= 1
            if end > 0:
                self._audio_callback(bytes(payload[:end]))
            self._last_audio_time = asyncio.get_event_loop().time()

        # Signal data reporting event (cmd 0x73) for thumbnail flow
        if cmd_id == CmdId.DATA_REPORTING:
            if hasattr(self, '_data_report_event'):
                self._data_report_event.set()

        # Parse device notify events from payload
        if len(payload) > 6:
            event = GlassesEvent(payload[6], bytes(payload))
            self._emit_event(event)

    def _on_small_notification(self, char: BleakGATTCharacteristic, data: bytearray):
        """Handle small data (16-byte) notifications."""
        logger.debug("RX small [%s]: %s", char.uuid[:8], data.hex())

    # ── High-level Commands ─────────────────────────────────────────────
    # All commands use the large data protocol: build_packet(CmdId, payload)
    # and send via serial port characteristic (de5bf72a).

    async def _glasses_control(self, payload: bytes, wait: bool = False) -> Optional[bytes]:
        """Send a glasses control command (cmd_id=0x41)."""
        return await self.send_command(CmdId.GLASSES_CONTROL, payload, wait_response=wait)

    async def take_photo(self):
        await self._glasses_control(bytes([0x02, 0x01, DeviceMode.PHOTO]))

    async def take_ai_photo(self, thumbnail_size: int = 0x02):
        await self._glasses_control(bytes([0x02, 0x01, DeviceMode.AI_PHOTO,
                                           thumbnail_size, thumbnail_size, 0x02]))

    async def start_video(self):
        await self._glasses_control(bytes([0x02, 0x01, DeviceMode.VIDEO]))

    async def stop_video(self):
        await self._glasses_control(bytes([0x02, 0x01, DeviceMode.VIDEO_STOP]))

    async def start_audio(self):
        await self._glasses_control(bytes([0x02, 0x01, DeviceMode.AUDIO]))

    async def stop_audio(self):
        await self._glasses_control(bytes([0x02, 0x01, DeviceMode.AUDIO_STOP]))

    async def start_speech_recognition(self):
        await self._glasses_control(bytes([0x02, 0x01, DeviceMode.SPEECH_RECOGNITION]))

    async def stop_speech_recognition(self):
        await self._glasses_control(bytes([0x02, 0x01, DeviceMode.SPEECH_RECOGNITION_STOP]))

    async def start_translation(self):
        await self._glasses_control(bytes([0x02, 0x01, DeviceMode.TRANSLATE_START]))

    async def stop_translation(self):
        await self._glasses_control(bytes([0x02, 0x01, DeviceMode.TRANSLATE_STOP]))

    async def speak_start(self):
        await self._glasses_control(bytes([0x02, 0x01, DeviceMode.SPEAK_START]))

    async def speak_stop(self):
        await self._glasses_control(bytes([0x02, 0x01, DeviceMode.SPEAK_STOP]))

    async def ai_speak(self, mode: AISpeakMode):
        await self.send_command(CmdId.VOICE_STATUS, bytes([mode]))

    async def get_battery(self) -> Optional[bytes]:
        return await self.send_command(CmdId.BATTERY, b"", wait_response=True)

    async def get_version(self) -> Optional[bytes]:
        return await self.send_command(CmdId.DEVICE_INFO, b"", wait_response=True)

    async def get_media_count(self) -> Optional[bytes]:
        return await self._glasses_control(bytes([0x02, 0x04]), wait=True)

    async def set_volume(self, mode: VolumeMode, level: int):
        await self.send_command(CmdId.VOLUME_CONTROL, bytes([mode, level]))

    async def find_device(self):
        await self._glasses_control(bytes([0x02, 0x01, DeviceMode.FIND_DEVICE]))

    async def enable_wifi_transfer(self):
        await self._glasses_control(bytes([0x02, 0x01, DeviceMode.TRANSFER]))

    async def disable_wifi_transfer(self):
        await self._glasses_control(bytes([0x02, 0x01, DeviceMode.TRANSFER_STOP]))

    async def get_thumbnail(self, timeout: float = 10.0) -> Optional[bytes]:
        """Get the latest photo thumbnail via BLE (JPEG bytes).

        Call take_ai_photo() first, then this to receive the thumbnail.
        """
        self._thumbnail_event.clear()
        await self.send_command(CmdId.PICTURE_THUMBNAILS, b"")
        try:
            await asyncio.wait_for(self._thumbnail_event.wait(), timeout)
            return self._thumbnail_data
        except asyncio.TimeoutError:
            logger.warning("Thumbnail response timeout")
            return None

    async def capture_and_get_thumbnail(self, thumbnail_size: int = 0x02,
                                         timeout: float = 15.0) -> Optional[bytes]:
        """Take an AI photo and get the thumbnail via BLE.

        Flow: take_ai_photo → wait for data reporting (0x73) → request thumbnail (0xFD).
        Returns JPEG bytes of the thumbnail, or None on timeout.
        """
        self._thumbnail_event.clear()
        self._data_report_event = asyncio.Event()
        await self.take_ai_photo(thumbnail_size)

        # Wait for data reporting (cmd=0x73) which signals photo is ready
        try:
            await asyncio.wait_for(self._data_report_event.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            pass
        # Extra settle time
        await asyncio.sleep(0.5)

        # Request thumbnail
        await self.send_command(CmdId.PICTURE_THUMBNAILS, b"")
        try:
            await asyncio.wait_for(self._thumbnail_event.wait(), timeout)
            return self._thumbnail_data
        except asyncio.TimeoutError:
            logger.warning("AI photo thumbnail timeout")
            return None

    def on_audio(self, callback: Optional[Callable[[bytes], None]]):
        """Set callback for raw OPUS frames from glasses mic.

        callback receives raw OPUS frame bytes (no header, no zero padding).
        Decode with opuslib.Decoder(16000, 1), frame_size=320.
        Set to None to stop receiving.
        """
        self._audio_callback = callback

    async def start_mic_stream(self):
        """Start speech recognition mode to stream mic audio via BLE."""
        await self.start_speech_recognition()

    async def stop_mic_stream(self):
        """Stop mic audio streaming."""
        await self.stop_speech_recognition()

    async def sync_time(self):
        """Sync device time."""
        import time
        ts = int(time.time())
        payload = ts.to_bytes(4, "little")
        await self.send_command(CmdId.SYNC_TIME, payload)
