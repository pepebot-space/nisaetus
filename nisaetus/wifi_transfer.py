"""
WiFi file transfer from HeyCyan glasses.
After enabling transfer mode, the glasses start a WiFi hotspot.
Connect to it, then download media files via HTTP.
"""

import asyncio
import logging
from pathlib import Path
from typing import Optional

import aiohttp

from .protocol import WIFI_CANDIDATE_IPS, MEDIA_CONFIG_PATH, MEDIA_FILES_PATH

logger = logging.getLogger(__name__)


async def find_glasses_ip(timeout: float = 3.0) -> Optional[str]:
    """Probe candidate IPs to find the glasses HTTP server."""
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout)) as session:
        for ip in WIFI_CANDIDATE_IPS:
            url = f"http://{ip}{MEDIA_CONFIG_PATH}"
            try:
                async with session.get(url) as resp:
                    if resp.status == 200:
                        logger.info("Glasses found at %s", ip)
                        return ip
            except Exception:
                continue
    return None


async def list_media(ip: str) -> list[str]:
    """Get list of media filenames from glasses."""
    url = f"http://{ip}{MEDIA_CONFIG_PATH}"
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            text = await resp.text()
            files = [f.strip() for f in text.strip().split("\n") if f.strip()]
            logger.info("Found %d media files", len(files))
            return files


async def download_file(ip: str, filename: str, dest_dir: str = "./media") -> Path:
    """Download a single media file from glasses."""
    dest = Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)
    filepath = dest / filename

    url = f"http://{ip}{MEDIA_FILES_PATH}{filename}"
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            data = await resp.read()
            filepath.write_bytes(data)
            logger.info("Downloaded %s (%d bytes)", filename, len(data))
            return filepath


async def download_latest_photo(ip: str, dest_dir: str = "./media") -> Optional[Path]:
    """Download the most recent photo from glasses."""
    files = await list_media(ip)
    photo_exts = (".jpg", ".jpeg", ".png", ".heic")
    photos = [f for f in files if any(f.lower().endswith(ext) for ext in photo_exts)]
    if not photos:
        logger.warning("No photos found on glasses")
        return None
    latest = photos[-1]
    return await download_file(ip, latest, dest_dir)
