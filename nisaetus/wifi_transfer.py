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

MEDIA_EXTENSIONS = (".jpg", ".jpeg", ".png", ".heic", ".mov", ".mp4", ".m4v", ".opus", ".wav")


def _is_valid_media_config(text: str) -> bool:
    """Check if response is a real media.config (file list), not an HTML page."""
    stripped = text.strip()
    if not stripped:
        return True  # empty is valid (no files)
    if stripped.startswith("<!") or stripped.startswith("<html") or "<head>" in stripped.lower():
        return False
    # At least one line should look like a media filename
    for line in stripped.split("\n"):
        line = line.strip()
        if line and any(line.lower().endswith(ext) for ext in MEDIA_EXTENSIONS):
            return True
    return False


async def find_glasses_ip(timeout: float = 3.0) -> Optional[str]:
    """Probe candidate IPs to find the glasses HTTP server."""
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout)) as session:
        for ip in WIFI_CANDIDATE_IPS:
            url = f"http://{ip}{MEDIA_CONFIG_PATH}"
            try:
                async with session.get(url) as resp:
                    if resp.status == 200:
                        text = await resp.text()
                        if _is_valid_media_config(text):
                            logger.info("Glasses found at %s", ip)
                            return ip
                        else:
                            logger.debug("Skipping %s (responded with HTML)", ip)
            except Exception:
                continue
    return None


async def list_media(ip: str) -> list[str]:
    """Get list of media filenames from glasses."""
    url = f"http://{ip}{MEDIA_CONFIG_PATH}"
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            text = await resp.text()
            files = [f.strip() for f in text.strip().split("\n")
                     if f.strip() and not f.strip().startswith("<")]
            logger.info("Found %d media files", len(files))
            return files


async def download_file(ip: str, filename: str, dest_dir: str = "./media") -> Path:
    """Download a single media file from glasses."""
    dest = Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)
    # Sanitize filename
    safe_name = Path(filename).name
    filepath = dest / safe_name

    url = f"http://{ip}{MEDIA_FILES_PATH}{filename}"
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            data = await resp.read()
            filepath.write_bytes(data)
            logger.info("Downloaded %s (%d bytes)", safe_name, len(data))
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
