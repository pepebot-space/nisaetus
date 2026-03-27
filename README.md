# Nisaetus

Real-time AI vision and voice assistant through HeyCyan Smart Glasses, powered by [Pepebot](https://github.com/pepebot-space/pepebot) Live API.

Nisaetus connects to HeyCyan smart glasses via BLE, captures photos from the glasses camera over WiFi, streams microphone audio to the Pepebot Live API, and plays back AI responses — enabling hands-free AI interaction through smart glasses.

## How It Works

```
┌─────────────────┐     BLE      ┌──────────────┐    WebSocket    ┌─────────────┐
│  HeyCyan Glasses │◄────────────►│   Nisaetus   │◄──────────────►│   Pepebot   │
│                  │   WiFi/HTTP  │   (Python)   │                │  Live API   │
│  - Camera        │◄────────────►│              │                │  (Gemini)   │
│  - Microphone    │              │  - BLE ctrl  │                └─────────────┘
│  - Speaker       │              │  - WiFi xfer │
└─────────────────┘              │  - Audio I/O │
                                  └──────────────┘
```

1. **BLE** — Connect to glasses, send commands (photo/video/audio/speaker control)
2. **WiFi Transfer** — Glasses creates a hotspot, Nisaetus downloads captured photos via HTTP
3. **Pepebot Live API** — JPEG frames + PCM audio streamed over WebSocket to Gemini for real-time multimodal AI
4. **Audio Playback** — AI audio responses played back locally

## Requirements

- Python 3.11 or 3.12
- [Poetry](https://python-poetry.org/)
- Bluetooth Low Energy adapter
- [Pepebot](https://github.com/pepebot-space/pepebot) gateway running with `live.enabled=true`
- PortAudio (`brew install portaudio` on macOS)

## Install

```bash
git clone git@github.com:pepebot-space/nisaetus.git
cd nisaetus
brew install portaudio  # macOS
poetry install
```

## Usage

```bash
# Default: connect to glasses via BLE, use glasses camera + local mic
poetry run nisaetus

# Skip BLE scan, connect directly by address
poetry run nisaetus --address AA:BB:CC:DD:EE:FF

# Local mode (no glasses, local mic only)
poetry run nisaetus --mode local

# Hybrid mode (glasses camera + local mic + local speaker)
poetry run nisaetus --mode hybrid

# Custom Pepebot URL
poetry run nisaetus --url ws://192.168.1.100:18790/v1/live

# Debug logging
poetry run nisaetus -v
```

### Modes

| Mode | Camera | Microphone | Speaker |
|------|--------|------------|---------|
| `glasses` | Glasses (WiFi) | Local mic | Local speaker |
| `hybrid` | Glasses (WiFi) | Local mic | Local speaker |
| `local` | None | Local mic | Local speaker |

### Glasses WiFi Connection

When running in `glasses` or `hybrid` mode, after BLE connection the glasses will start a WiFi hotspot. Connect your computer to it:

- **Password:** `123456789`
- Nisaetus will auto-detect the glasses IP (tries `192.168.43.1`, `192.168.4.1`, etc.)

## Project Structure

```
nisaetus/
├── __init__.py
├── protocol.py         # BLE protocol (packet framing, CRC16, command IDs)
├── glasses.py          # HeyCyan BLE client (scan, connect, commands)
├── wifi_transfer.py    # HTTP media download from glasses hotspot
├── live_client.py      # Pepebot Live API WebSocket integration
└── cli.py              # CLI entry point
scripts/
└── test_glasses.py     # Standalone test script for glasses commands
```

## BLE Protocol

Reverse-engineered from the [HeyCyan Smart Glasses SDK](https://github.com/anak10thn/HeyCyanSmartGlassesSDK) (oudmon BLE library).

### Channels

| Channel | Service UUID | Write Char | Notify Char | Purpose |
|---------|---|---|---|---|
| Serial Port | `de5bf728-d711-4e47-af26-65e3012a5dc7` | `de5bf72a` | `de5bf729` | Main command protocol (camera, battery, AI) |
| Small Data | `6e40fff0-b5a3-f393-e0a9-e50e24dcca9e` | `6e400002` | `6e400003` | 16-byte fixed packets |

### Packet Format (Serial Port)

All commands use the serial port channel with this framing:

```
[0xBC] [cmd_id] [len_lo] [len_hi] [crc16_lo] [crc16_hi] [payload...]
```

- Magic byte: `0xBC`
- CRC16/MODBUS computed over payload
- Packets > 244 bytes are fragmented into chunks

### Command IDs

| ID | Hex | Purpose |
|----|-----|---------|
| 64 | `0x40` | Sync time |
| 65 | `0x41` | Glasses control (photo/video/audio/transfer) |
| 66 | `0x42` | Battery query |
| 67 | `0x43` | Device info |
| 68 | `0x44` | AI voice |
| 72 | `0x48` | Voice status |
| 81 | `0x51` | Volume control |

### Glasses Control Payloads (cmd 0x41)

| Command | Payload | Description |
|---------|---------|-------------|
| Take photo | `02 01 01` | Capture photo |
| Start video | `02 01 02` | Start recording |
| Stop video | `02 01 03` | Stop recording |
| WiFi transfer | `02 01 04` | Enable WiFi hotspot |
| AI photo | `02 01 06 sz sz 02` | AI capture + thumbnail |
| Start audio | `02 01 08` | Record audio (mic) |
| Stop audio | `02 01 0C` | Stop audio recording |
| Find device | `02 01 0D` | Beep/flash |
| Speaker start | `02 01 10` | Start voice playback |
| Speaker stop | `02 01 11` | Stop voice playback |

## License

MIT
