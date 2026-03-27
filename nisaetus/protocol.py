"""
HeyCyan Smart Glasses BLE Protocol
Based on QCSDK analysis: service UUIDs, command bytes, notification parsing.
"""

from enum import IntEnum


# ── BLE Service UUIDs ───────────────────────────────────────────────────────
# Discovered from M02S/A02S glasses
SERVICE_MAIN = "0000ae30-0000-1000-8000-00805f9b34fb"       # Main command service
SERVICE_QCSDK = "6e40fff0-b5a3-f393-e0a9-e50e24dcca9e"     # QCSDK Nordic UART
SERVICE_EXTRA = "de5bf728-d711-4e47-af26-65e3012a5dc7"      # Extra channel
SERVICE_DFU = "0000ae3a-0000-1000-8000-00805f9b34fb"        # DFU/OTA
SERVICE_PAIR = "00003802-0000-1000-8000-00805f9b34fb"       # Pairing service

# From older SDK docs (may appear on other models)
SERVICE_PRIMARY_LEGACY = "7905fff0-b5ce-4e99-a40f-4b1e122d00d0"

ALL_KNOWN_SERVICES = [
    SERVICE_MAIN, SERVICE_QCSDK, SERVICE_EXTRA,
    SERVICE_DFU, SERVICE_PAIR, SERVICE_PRIMARY_LEGACY,
]

# ── Characteristic UUIDs (from M02S/A02S discovery) ─────────────────────────
# Main service (ae30)
CHAR_CMD_WRITE = "0000ae01-0000-1000-8000-00805f9b34fb"     # write-without-response
CHAR_CMD_NOTIFY = "0000ae02-0000-1000-8000-00805f9b34fb"    # notify
CHAR_DATA_WRITE = "0000ae03-0000-1000-8000-00805f9b34fb"    # write-without-response (data)
CHAR_DATA_NOTIFY = "0000ae04-0000-1000-8000-00805f9b34fb"   # notify (data)
CHAR_INDICATE = "0000ae05-0000-1000-8000-00805f9b34fb"      # indicate
CHAR_RW = "0000ae10-0000-1000-8000-00805f9b34fb"            # read, write

# QCSDK Nordic UART service (6e40fff0)
CHAR_QCSDK_WRITE = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"  # write, write-without-response
CHAR_QCSDK_NOTIFY = "6e400003-b5a3-f393-e0a9-e50e24dcca9e" # notify

# Extra channel (de5bf728)
CHAR_EXTRA_WRITE = "de5bf72a-d711-4e47-af26-65e3012a5dc7"   # write, write-without-response
CHAR_EXTRA_NOTIFY = "de5bf729-d711-4e47-af26-65e3012a5dc7"  # notify

# Pairing service (3802)
CHAR_PAIR = "00004a02-0000-1000-8000-00805f9b34fb"          # read, write, notify

# Known device name patterns
DEVICE_NAME_PATTERNS = ["m02s", "a02s", "heycyan", "cyan", "glasses", "odm", "qc"]


# ── Device Modes (QCOperatorDeviceMode) ─────────────────────────────────────

class DeviceMode(IntEnum):
    UNKNOWN = 0x00
    PHOTO = 0x01
    VIDEO = 0x02
    VIDEO_STOP = 0x03
    TRANSFER = 0x04
    OTA = 0x05
    AI_PHOTO = 0x06
    SPEECH_RECOGNITION = 0x07
    AUDIO = 0x08
    TRANSFER_STOP = 0x09
    FACTORY_RESET = 0x0A
    SPEECH_RECOGNITION_STOP = 0x0B
    AUDIO_STOP = 0x0C
    FIND_DEVICE = 0x0D
    RESTART = 0x0E
    NO_POWER_P2P = 0x0F
    SPEAK_START = 0x10
    SPEAK_STOP = 0x11
    TRANSLATE_START = 0x12
    TRANSLATE_STOP = 0x13


# ── Command Opcodes (ODM_DFU_Operation) ─────────────────────────────────────

class Opcode(IntEnum):
    SETUP_DEVICE_STATUS = 0x40
    SET_DEVICE_MODE = 0x41
    GET_BATTERY = 0x42
    GET_VERSION = 0x43
    VOICE_WAKEUP = 0x44
    VOICE_HEARTBEAT = 0x45
    WEARING_DETECTION = 0x46
    DEVICE_CONFIG = 0x47
    AI_SPEAK = 0x48
    VOLUME = 0x51
    BT_STATUS = 0x52
    THUMBNAIL = 0xFD
    DATA_UPDATE = 0x73


class AISpeakMode(IntEnum):
    START = 0x01
    HOLD = 0x02
    STOP = 0x03
    THINKING_START = 0x04
    THINKING_HOLD = 0x05
    THINKING_STOP = 0x06
    NO_NET = 0xF1


class VolumeMode(IntEnum):
    MUSIC = 0x01
    CALL = 0x02
    SYSTEM = 0x03


class NotifyEvent(IntEnum):
    AI_RECOGNITION = 0x02
    MICROPHONE = 0x03
    OTA_PROGRESS = 0x04
    BATTERY = 0x05
    VOICE_PAUSE = 0x0C
    UNBIND = 0x0D
    LOW_MEMORY = 0x0E
    TRANSLATE_PAUSE = 0x10
    VOLUME_CHANGE = 0x12


# ── Raw Command Builders ────────────────────────────────────────────────────

def cmd_set_mode(mode: DeviceMode) -> bytes:
    """Build set device mode command: [0x02, 0x01, mode]"""
    return bytes([0x02, 0x01, mode])


def cmd_ai_photo(thumbnail_size: int = 0x02) -> bytes:
    """Build AI photo command with thumbnail request."""
    return bytes([0x02, 0x01, DeviceMode.AI_PHOTO, thumbnail_size, thumbnail_size, 0x02])


def cmd_get_battery() -> bytes:
    return bytes([0x02, Opcode.GET_BATTERY])


def cmd_get_version() -> bytes:
    return bytes([0x02, Opcode.GET_VERSION])


def cmd_get_media_count() -> bytes:
    return bytes([0x02, 0x04])


def cmd_set_volume(mode: VolumeMode, level: int) -> bytes:
    return bytes([0x02, Opcode.VOLUME, mode, level])


def cmd_ai_speak(mode: AISpeakMode) -> bytes:
    return bytes([0x02, Opcode.AI_SPEAK, mode])


# ── WiFi Transfer Constants ─────────────────────────────────────────────────

# ── Large Data Packet Format ─────────────────────────────────────────────────
# All commands go through de5bf72a with this framing:
#   [0xBC] [cmd_id] [len_lo] [len_hi] [crc16_lo] [crc16_hi] [payload...]

MAGIC_BYTE = 0xBC
MAX_CHUNK_SIZE = 244  # MTU chunk size for BLE writes


class CmdId(IntEnum):
    """Large data command IDs."""
    BT_MAC = 0x2E
    SYNC_TIME = 0x40
    GLASSES_CONTROL = 0x41
    BATTERY = 0x42
    DEVICE_INFO = 0x43
    AI_VOICE = 0x44
    HEARTBEAT = 0x45
    DEVICE_WEAR = 0x46
    WEAR_SUPPORT = 0x47
    VOICE_STATUS = 0x48
    BT_CONNECT = 0x49
    VOLUME_CONTROL = 0x51
    SPEAK_SOUND_SWITCH = 0x52
    GPT_UPLOAD = 0x59
    DATA_REPORTING = 0x73
    PICTURE_THUMBNAILS = 0xFD
    OTA_SOC = 0xFC


def crc16_modbus(data: bytes) -> int:
    """CRC-16/MODBUS over payload bytes."""
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            if crc & 1:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    return crc & 0xFFFF


def build_packet(cmd_id: int, payload: bytes) -> bytes:
    """Build a framed packet: [0xBC][cmd_id][len_le16][crc16_le16][payload]"""
    length = len(payload)
    crc = crc16_modbus(payload) if payload else 0xFFFF
    header = bytes([
        MAGIC_BYTE,
        cmd_id & 0xFF,
        length & 0xFF, (length >> 8) & 0xFF,
        crc & 0xFF, (crc >> 8) & 0xFF,
    ])
    return header + payload


def parse_packet(data: bytes) -> tuple[int, bytes] | None:
    """Parse incoming packet. Returns (cmd_id, payload) or None."""
    if len(data) < 6 or data[0] != MAGIC_BYTE:
        return None
    cmd_id = data[1]
    length = data[2] | (data[3] << 8)
    if len(data) < 6 + length:
        return None
    payload = data[6:6 + length]
    return (cmd_id, payload)


# ── Small Data Packet Format (16-byte fixed, channel 6e400002) ──────────────
# [key: 1 byte] [sub_data: 14 bytes zero-padded] [checksum: 1 byte]
# checksum = sum(bytes[0..14]) & 0xFF

SMALL_PACKET_SIZE = 16


def build_small_packet(key: int, sub_data: bytes = b"") -> bytes:
    """Build a 16-byte small data packet for 6e400002."""
    pkt = bytearray(SMALL_PACKET_SIZE)
    pkt[0] = key & 0xFF
    for i, b in enumerate(sub_data[:14]):
        pkt[1 + i] = b
    pkt[15] = sum(pkt[:15]) & 0xFF
    return bytes(pkt)


# ── WiFi Transfer Constants ─────────────────────────────────────────────────

WIFI_DEFAULT_PASSWORD = "123456789"
WIFI_CANDIDATE_IPS = [
    "192.168.43.1", "192.168.4.1", "192.168.31.1",
    "192.168.1.1", "192.168.0.1", "192.168.100.1",
    "192.168.123.1", "192.168.137.1", "10.0.0.1", "172.20.10.1",
]
MEDIA_CONFIG_PATH = "/files/media.config"
MEDIA_FILES_PATH = "/files/"
