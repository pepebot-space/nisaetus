# Nisaetus Architecture

Technical documentation of the HeyCyan Smart Glasses BLE protocol and Pepebot Live API integration, based on reverse engineering the [HeyCyanSmartGlassesSDK](https://github.com/anak10thn/HeyCyanSmartGlassesSDK).

## Device Info

- **Model:** M02S / A02S
- **Hardware:** A02S_V1.0
- **BT Firmware:** A02S_1.00.17_251030
- **WiFi Firmware:** WIFIA02S_3.00.02_251201
- **BLE Chip:** oudmon (Anhui Huami / Zepp ecosystem)
- **Advertised Name:** `M02S_D1BA` (pattern: `M02S_<MAC_SUFFIX>`)

## BLE Service Map

Discovered via `BleakClient.services` after connecting:

| Service UUID | Description | Characteristics |
|---|---|---|
| `0000ae30-...-00805f9b34fb` | Main (oudmon vendor) | ae01 (write-no-resp), ae02 (notify), ae03 (write-no-resp), ae04 (notify), ae05 (indicate), ae10 (read/write) |
| `00003802-...-00805f9b34fb` | Pairing | 4a02 (read/write/notify) |
| `0000ae3a-...-00805f9b34fb` | DFU/OTA | ae3b (write-no-resp), ae3c (notify) |
| `6e40fff0-b5a3-f393-e0a9-e50e24dcca9e` | QCSDK Nordic UART | 6e400002 (write), 6e400003 (notify) |
| `de5bf728-d711-4e47-af26-65e3012a5dc7` | Serial Port (Large Data) | de5bf72a (write), de5bf729 (notify) |
| `0000180a-...-00805f9b34fb` | Device Information | 2a25 (serial), 2a26 (fw rev), 2a27 (hw rev), 2a23 (system id) |
| `0000fee1-...-00805f9b34fb` | Anhui Huami | fee3 (read/write/notify) |

## Channel Discovery

Tested by sending commands on each channel and observing responses:

| Channel | Write → Notify | Behavior |
|---|---|---|
| ae01 → ae02 | **Echo only** — glasses echoes raw bytes without processing |
| ae03 → ae04 | No response |
| 6e400002 → 6e400003 | No response (Small Data channel, 16-byte fixed packets) |
| **de5bf72a → de5bf729** | **Correct channel** — processes commands with `0xBC` framing |

**Key finding:** The `ae01/ae02` channel is a transparent UART bridge that echoes everything. The actual command processing happens on the `de5bf72a/de5bf729` serial port channel with packet framing.

## Packet Protocol (Large Data)

Reverse-engineered by decompiling the Android `glasses_sdk_20250723_v01.aar` (oudmon BLE SDK).

### Framing Format

```
Offset  Size   Field
------  -----  -----
0       1      Magic byte: 0xBC
1       1      Command ID (see CmdId table)
2       2      Payload length (little-endian uint16)
4       2      CRC16/MODBUS of payload (little-endian uint16)
6       N      Payload data
```

If payload is empty, CRC bytes are `0xFF 0xFF`.

### CRC16 Algorithm

CRC-16/MODBUS:
- Initial: `0xFFFF`
- Polynomial: `0xA001` (reflected)
- Computed over payload bytes only

### Packet Fragmentation

Packets larger than 244 bytes are split into chunks. Each chunk is written as `WRITE_NO_RESPONSE` to `de5bf72a`. The receiver reassembles by:
1. Detecting `0xBC` magic byte at start
2. Reading payload length from bytes [2..3]
3. Buffering until `total_length == payload_length + 6`

### Example: Take Photo

```
Payload: 02 01 01
CRC16([02 01 01]) = 0x5510

Packet: BC 41 03 00 10 55 02 01 01
         │  │  │     │     └─ payload
         │  │  │     └─ CRC16 LE
         │  │  └─ length = 3
         │  └─ cmd 0x41 (GLASSES_CONTROL)
         └─ magic 0xBC
```

## Command IDs

| ID | Hex | Name | Description |
|----|-----|------|-------------|
| 46 | 0x2E | BT_MAC | Bluetooth MAC address |
| 64 | 0x40 | SYNC_TIME | Sync device clock (payload: unix timestamp LE32) |
| 65 | 0x41 | GLASSES_CONTROL | Camera/video/audio/transfer mode control |
| 66 | 0x42 | BATTERY | Query battery level |
| 67 | 0x43 | DEVICE_INFO | Query firmware/hardware versions |
| 68 | 0x44 | AI_VOICE | AI voice wakeup |
| 69 | 0x45 | HEARTBEAT | Keep-alive heartbeat |
| 70 | 0x46 | DEVICE_WEAR | Wearing detection |
| 71 | 0x47 | WEAR_SUPPORT | Wearing detection config |
| 72 | 0x48 | VOICE_STATUS | AI speak mode control |
| 73 | 0x49 | BT_CONNECT | Bluetooth classic connection |
| 81 | 0x51 | VOLUME_CONTROL | Volume get/set |
| 82 | 0x52 | SPEAK_SOUND_SWITCH | Speaker sound switch |
| 89 | 0x59 | GPT_UPLOAD | **Mic audio stream** (OPUS frames from glasses) |
| 115 | 0x73 | DATA_REPORTING | Device status/event reporting |
| 253 | 0xFD | PICTURE_THUMBNAILS | AI photo thumbnail (JPEG via BLE) |
| 252 | 0xFC | OTA_SOC | OTA firmware update |

## Glasses Control Payloads (cmd 0x41)

The GLASSES_CONTROL command uses payloads starting with `[0x02, 0x01, mode]`:

| Mode | Hex | Payload | Description |
|------|-----|---------|-------------|
| Photo | 0x01 | `02 01 01` | Capture photo |
| Video | 0x02 | `02 01 02` | Start video recording |
| Video Stop | 0x03 | `02 01 03` | Stop video recording |
| Transfer | 0x04 | `02 01 04` | Enable WiFi hotspot (P2P) |
| AI Photo | 0x06 | `02 01 06 sz sz 02` | AI capture + thumbnail |
| Speech Recognition | 0x07 | `02 01 07` | Start mic → AI audio stream |
| Audio | 0x08 | `02 01 08` | Start audio recording |
| Transfer Stop | 0x09 | `02 01 09` | Disable WiFi |
| Speech Recog Stop | 0x0B | `02 01 0B` | Stop speech recognition |
| Audio Stop | 0x0C | `02 01 0C` | Stop audio recording |
| Find Device | 0x0D | `02 01 0D` | Beep/flash |
| Restart | 0x0E | `02 01 0E` | Restart glasses |
| Speak Start | 0x10 | `02 01 10` | Start voice playback on speaker |
| Speak Stop | 0x11 | `02 01 11` | Stop voice playback |
| Translate Start | 0x12 | `02 01 12` | Start translation |
| Translate Stop | 0x13 | `02 01 13` | Stop translation |
| Media Count | — | `02 04` | Get photo/video/audio file counts |

### Response Format (cmd 0x41)

Response payload: `[02] [dataType] [data...]`

| dataType | Fields | Description |
|----------|--------|-------------|
| 1 | workTypeIng, errorCode | Current mode + status |
| 3 | IP address bytes | WiFi P2P IP |
| 4 | imageCount(2B), videoCount(2B), recordCount(2B) | Media file counts |

### WiFi Transfer Response

When Transfer mode (0x04) is activated, response contains SSID and password:

```
Payload: 02 01 04 01 [ssid_len LE16] [pass_len LE16] [ssid_bytes] [pass_bytes]

Example: 02 01 04 01 11 00 09 00 4d3032535f413343313139364144314241 313233343536373839
         → SSID: "M02S_A3C1196AD1BA", Password: "123456789"
```

**Note:** WiFi transfer uses WiFi Direct (P2P), not a standard hotspot. macOS does not support WiFi Direct natively.

## Battery Response (cmd 0x42)

```
Payload: [battery_level: uint8] [charging: uint8]
Example: 64 00 → 100%, not charging
```

## Device Info Response (cmd 0x43)

Payload contains firmware version strings concatenated:
```
BT firmware: "A02S_1.00.17_251030"
BT hardware: "A02S_V1.0"
WiFi firmware: "WIFIA02S_3.00.02_251201"
WiFi hardware: "WIFIA02S_V1.0"
```

## AI Photo Thumbnail (cmd 0xFD)

### Flow

1. Send AI Photo command: `cmd=0x41 payload=[02 01 06 02 02 02]`
2. Wait for Data Reporting: `cmd=0x73` notification (photo ready)
3. Request thumbnail: `cmd=0xFD payload=[]`
4. Receive thumbnail: `cmd=0xFD payload=[header...] [FFD8 JPEG data]`

### Response

Payload starts with a 5-byte header, then JPEG data:

```
01 0c 00 00 00 FF D8 FF E0 ... (JPEG)
                ^^^^^^^^^^^
                Find 0xFFD8 to extract JPEG
```

Thumbnail is ~1KB JPEG (small resolution for BLE bandwidth).

**Important:** AI Photo mode (0x06) and Speech Recognition mode (0x07) conflict — glasses can only run one mode at a time.

## Mic Audio Stream (cmd 0x59 — GPT_UPLOAD)

### Discovery

When Speech Recognition mode (`0x07`) is activated, the glasses stream audio from the built-in microphone via BLE as `cmd=0x59` packets.

### Packet Format

- Fixed **40 bytes** per packet
- **~48 packets/second** (~20.8ms per frame)
- Payload: raw OPUS frame zero-padded to 40 bytes
- No header to skip (byte 0 is part of the OPUS TOC byte)
- Trailing zeros are padding, strip before decoding

```
[OPUS frame data (variable 5-38 bytes)] [zero padding to 40 bytes]
```

### Decoding

```python
import opuslib

decoder = opuslib.Decoder(16000, 1)  # 16kHz mono

for packet in audio_packets:
    # Strip trailing zeros
    end = len(packet)
    while end > 0 and packet[end-1] == 0:
        end -= 1
    if end < 1:
        continue

    frame = packet[:end]
    pcm = decoder.decode(frame, 320)  # 320 samples = 20ms @ 16kHz
```

### Parameters

| Parameter | Value |
|-----------|-------|
| Codec | OPUS |
| Sample rate | 16000 Hz |
| Channels | 1 (mono) |
| Frame duration | ~20ms |
| Frame size | 320 samples |
| Bitrate | ~15 kbps |
| Decode success rate | ~88% (silent frames may fail) |

### Behavior

- Glasses auto-stop speech recognition after ~5-6 seconds of silence
- Requires auto-restart logic for continuous streaming
- `cmd=0x73 payload=0301` notification = mic started
- `cmd=0x41 payload=02010bffff` notification = speech recognition stopped

## Small Data Protocol (channel 6e400002)

16-byte fixed packets:

```
Offset  Size  Field
------  ----  -----
0       1     Command Key
1       14    Sub-data (zero-padded)
15      1     Checksum: sum(bytes[0..14]) & 0xFF
```

Used by `CommandHandle` for simple commands. Currently not utilized in Nisaetus (all commands go through the serial port large data channel).

## Data Reporting Events (cmd 0x73)

Asynchronous notifications from glasses:

| Payload | Description |
|---------|-------------|
| `03 01` | Microphone started |
| `01 XX 00 00 00 00 00 01` | Photo/media count update |
| `02 00 0c 01 00` | AI photo captured |
| `0b XX` | WiFi hotspot status |
| `09 ff 02` | Device status change |

## Notification Events (via GlassesDeviceNotifyListener)

When response `loadData[6]` (payload byte index 6) contains:

| Event | Hex | Data Fields |
|-------|-----|-------------|
| AI Recognition | 0x02 | loadData[9]==0x02: set recognition intent |
| Microphone | 0x03 | loadData[7]==1: mic active |
| OTA Progress | 0x04 | loadData[7]: download%, [8]: SOC%, [9]: NOR% |
| Battery | 0x05 | loadData[7]: level (0-100), [8]: charging flag |
| Voice Pause | 0x0C | loadData[7]==1: playback paused |
| Unbind | 0x0D | loadData[7]==1: app unbind request |
| Low Memory | 0x0E | — |
| Translate Pause | 0x10 | — |
| Volume Change | 0x12 | [8-10]: music min/max/cur, [12-14]: call, [16-18]: system, [19]: mode |

## System Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                        HeyCyan Smart Glasses                         │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────────────┐    │
│  │  Camera   │  │   Mic    │  │ Speaker  │  │  WiFi (P2P only) │    │
│  └────┬─────┘  └────┬─────┘  └────▲─────┘  └────────▲─────────┘    │
│       │              │             │                  │              │
│  ┌────▼──────────────▼─────────────┴──────────────────┴──────────┐  │
│  │                    BLE Serial Port (de5bf72a/de5bf729)         │  │
│  │                    Protocol: 0xBC framed packets                │  │
│  └───────────────────────────┬───────────────────────────────────┘  │
└──────────────────────────────┼──────────────────────────────────────┘
                               │ BLE
                               │
┌──────────────────────────────▼──────────────────────────────────────┐
│                          Nisaetus (Python)                           │
│                                                                      │
│  ┌─────────────┐  ┌─────────────────┐  ┌─────────────────────────┐  │
│  │ glasses.py   │  │ live_client.py  │  │ protocol.py             │  │
│  │              │  │                 │  │                         │  │
│  │ BLE scan     │  │ OPUS decode     │  │ build_packet()          │  │
│  │ connect      │  │ PCM 16kHz       │  │ parse_packet()          │  │
│  │ send_command │  │ WebSocket       │  │ crc16_modbus()          │  │
│  │ notifications│  │ audio/video     │  │ CmdId, DeviceMode       │  │
│  └──────┬───────┘  └────────┬────────┘  └─────────────────────────┘  │
│         │                   │                                        │
│         │            ┌──────▼────────┐                               │
│         │            │  PyAudio      │                               │
│         │            │  Speaker out  │                               │
│         │            └───────────────┘                               │
└─────────┼───────────────────┼───────────────────────────────────────┘
          │                   │ WebSocket
          │                   │
          │    ┌──────────────▼──────────────┐
          │    │   Pepebot Live API Gateway   │
          │    │   ws://localhost:18790/v1/live│
          │    │                              │
          │    │   ┌────────────────────────┐ │
          │    │   │ Gemini Live 2.5 Flash  │ │
          │    │   │ (native audio)         │ │
          │    │   └────────────────────────┘ │
          │    └──────────────────────────────┘
          │
     Glasses mic audio         AI audio response
     (OPUS → PCM 16kHz)        (PCM 24kHz → speaker)
     + AI photo thumbnails
       (JPEG via BLE)
```

## Data Flow

### Audio (Glasses Mic → AI)

```
Glasses Mic
    │
    ▼ OPUS encoded, 40-byte packets @ 48fps
cmd=0x59 (GPT_UPLOAD) via BLE serial port
    │
    ▼ Strip trailing zeros
opuslib.Decoder(16000, 1).decode(frame, 320)
    │
    ▼ PCM 16-bit signed LE, 16kHz mono
Batch to ~4096 byte chunks
    │
    ▼ base64 encode
WebSocket → Pepebot → Gemini
    │
    ▼ AI processes audio
WebSocket ← Pepebot ← Gemini
    │
    ▼ PCM 24kHz audio response
PyAudio speaker output
```

### Video (Glasses Camera → AI)

```
cmd=0x41 payload=[02 01 06 ...] (AI Photo)
    │
    ▼ Glasses captures photo
cmd=0x73 (DATA_REPORTING) notification
    │
    ▼ Photo ready
cmd=0xFD (PICTURE_THUMBNAILS) request
    │
    ▼ JPEG thumbnail (~1KB) via BLE
base64 encode
    │
    ▼ image/jpeg
WebSocket → Pepebot → Gemini
```

**Note:** Camera (AI Photo) and Mic (Speech Recognition) cannot run simultaneously — they are separate glasses modes. In `glasses` mode, Nisaetus prioritizes mic streaming.

## Debugging Commands

### BLE Scan

```bash
# Scan all BLE devices (no filter)
poetry run python -c "
import asyncio
from bleak import BleakScanner
async def main():
    devices = await BleakScanner.discover(timeout=15.0)
    for d in devices:
        print(f'{d.name or \"(no name)\"} [{d.address}] RSSI={d.rssi}')
asyncio.run(main())
"
```

### Connect and Dump Services

```bash
# Connect and show all services/characteristics
poetry run python -c "
import asyncio
from bleak import BleakClient, BleakScanner
async def main():
    device = await BleakScanner.find_device_by_name('M02S_D1BA', timeout=15.0)
    async with BleakClient(device.address) as client:
        for service in client.services:
            print(f'Service: {service.uuid}')
            for char in service.characteristics:
                print(f'  Char: {char.uuid} [{', '.join(char.properties)}]')
                if 'read' in char.properties:
                    val = await client.read_gatt_char(char)
                    print(f'    Value: {val.hex()}')
asyncio.run(main())
"
```

### Test Commands

```bash
# Test glasses commands (scan, connect, battery, photo, beep)
poetry run python scripts/test_glasses.py
```

### Send Raw Command

```bash
# Send any command via the correct serial port protocol
poetry run python -c "
import asyncio
from nisaetus.glasses import HeyCyanGlasses
from nisaetus.protocol import CmdId
async def main():
    g = HeyCyanGlasses()
    devs = await g.scan(timeout=10.0)
    await g.connect(devs[0][0].address)

    # Example: Get battery
    resp = await g.send_command(CmdId.BATTERY, b'', wait_response=True)
    print(f'Battery: {g.battery_level}%')

    # Example: Take photo
    await g.send_command(CmdId.GLASSES_CONTROL, bytes([0x02, 0x01, 0x01]))

    # Example: Find device (beep)
    await g.send_command(CmdId.GLASSES_CONTROL, bytes([0x02, 0x01, 0x0D]))

    await g.disconnect()
asyncio.run(main())
"
```

### Record Glasses Mic Audio

```bash
# Record 5 seconds of audio from glasses mic and save as WAV
poetry run python -c "
import asyncio, wave, opuslib
from nisaetus.glasses import HeyCyanGlasses
from nisaetus.protocol import parse_packet
async def main():
    g = HeyCyanGlasses()
    devs = await g.scan(timeout=10.0)
    await g.connect(devs[0][0].address)

    pkts = []
    orig = g._process_packet
    def capture(data):
        p = parse_packet(data)
        if p and p[0] == 0x59: pkts.append(p[1])
        orig(data)
    g._process_packet = capture

    print('Speak for 5 seconds...')
    await g.start_speech_recognition()
    await asyncio.sleep(5)
    await g.stop_speech_recognition()
    await asyncio.sleep(1)
    await g.disconnect()

    dec = opuslib.Decoder(16000, 1)
    pcm = bytearray()
    for p in pkts:
        end = len(p)
        while end > 0 and p[end-1] == 0: end -= 1
        if end < 1: continue
        try: pcm.extend(dec.decode(bytes(p[:end]), 320))
        except: pass

    with wave.open('recording.wav', 'wb') as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
        w.writeframes(bytes(pcm))
    print(f'Saved recording.wav ({len(pcm)} bytes)')
asyncio.run(main())
"
```

### Capture AI Photo Thumbnail

```bash
# Take AI photo and download thumbnail via BLE
poetry run python -c "
import asyncio
from nisaetus.glasses import HeyCyanGlasses
async def main():
    g = HeyCyanGlasses()
    devs = await g.scan(timeout=10.0)
    await g.connect(devs[0][0].address)

    jpeg = await g.capture_and_get_thumbnail(timeout=10.0)
    if jpeg and jpeg[:2] == b'\xff\xd8':
        with open('thumbnail.jpg', 'wb') as f: f.write(jpeg)
        print(f'Saved thumbnail.jpg ({len(jpeg)} bytes)')
    else:
        print('No thumbnail received')
    await g.disconnect()
asyncio.run(main())
"
```

### Run Live API Session

```bash
# Full session: glasses mic + Pepebot Live API
poetry run nisaetus --mode glasses

# With debug logging
poetry run nisaetus --mode glasses -v

# Local mic only (no glasses)
poetry run nisaetus --mode local

# Hybrid (glasses camera + local mic)
poetry run nisaetus --mode hybrid
```

## Known Issues

1. **WiFi Transfer uses P2P** — Glasses WiFi uses WiFi Direct, not a standard hotspot. macOS does not support WiFi Direct. Android uses `WifiP2pManager`.

2. **Mode Conflict** — Glasses can only run one mode at a time. Speech Recognition (mic stream) and AI Photo (camera thumbnail) cannot run simultaneously.

3. **Speech Recognition Auto-Stop** — Glasses auto-stop speech recognition after ~5-6 seconds of silence. Nisaetus auto-restarts with a 3-second timeout.

4. **OPUS Decode Rate** — About 88% of OPUS frames decode successfully. Silent/DTX frames may fail decoding, which is acceptable for voice streaming.

5. **BLE Reconnection** — After disconnect, glasses may take 10-30 seconds to become discoverable again. Restart glasses if scan fails.
