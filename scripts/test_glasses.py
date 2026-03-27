"""
Test: scan, connect, and send commands to glasses.
Uses the correct serial port protocol (0xBC framing on de5bf72a).
"""

import asyncio
import logging
from nisaetus.glasses import HeyCyanGlasses
from nisaetus.protocol import parse_packet

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


async def main():
    glasses = HeyCyanGlasses()

    glasses.on_event(lambda e: print(f"  EVENT type=0x{e.event_type:02X} data={e.raw.hex()}"))

    # Scan
    devices = await glasses.scan(timeout=10.0)
    if not devices:
        print("No glasses found!")
        return

    print(f"\nFound {len(devices)} device(s):")
    for i, (dev, adv) in enumerate(devices):
        print(f"  [{i}] {dev.name or 'Unknown'} ({dev.address}) RSSI: {adv.rssi}")

    dev = devices[0][0]
    print(f"\nConnecting to {dev.name}...")
    await glasses.connect(dev.address)
    print("Connected!\n")

    # Sync time
    print("--- Sync Time ---")
    await glasses.sync_time()
    await asyncio.sleep(1)

    # Battery
    print("\n--- Get Battery ---")
    await glasses.get_battery()
    print(f"  Battery: {glasses.battery_level}%")
    await asyncio.sleep(1)

    # Version
    print("\n--- Get Version ---")
    resp = await glasses.get_version()
    if resp:
        parsed = parse_packet(resp)
        if parsed:
            try:
                print(f"  Firmware: {parsed[1].decode('utf-8', errors='replace')}")
            except Exception:
                print(f"  Raw: {parsed[1].hex()}")
    await asyncio.sleep(1)

    # Find device
    print("\n--- Find Device (beep) ---")
    await glasses.find_device()
    await asyncio.sleep(3)

    # Take photo
    print("\n--- Take Photo ---")
    await glasses.take_photo()
    await asyncio.sleep(3)

    # Media count
    print("\n--- Media Count ---")
    await glasses.get_media_count()
    await asyncio.sleep(1)

    print(f"\nBattery: {glasses.battery_level}%")
    print("Done!")
    await glasses.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
