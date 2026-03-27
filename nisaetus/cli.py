"""
Nisaetus CLI - HeyCyan Smart Glasses + Pepebot Live API
"""

import argparse
import asyncio
import logging
import sys

from .live_client import NisaetusLive, DEFAULT_URL


async def async_main(args):
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    client = NisaetusLive(url=args.url, mode=args.mode)
    await client.run(glasses_address=args.address)


def main_entry():
    parser = argparse.ArgumentParser(
        prog="nisaetus",
        description="HeyCyan Smart Glasses + Pepebot Live API",
    )
    parser.add_argument(
        "--url", default=DEFAULT_URL,
        help=f"Pepebot Live API WebSocket URL (default: {DEFAULT_URL})",
    )
    parser.add_argument(
        "--mode", choices=["glasses", "hybrid", "local"], default="glasses",
        help="Input mode: glasses (camera+mic via glasses), hybrid (glasses camera + local mic), local (all local)",
    )
    parser.add_argument(
        "--address", default=None,
        help="BLE address of HeyCyan glasses (skip scan)",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true",
        help="Enable debug logging",
    )
    args = parser.parse_args()

    try:
        asyncio.run(async_main(args))
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    main_entry()
