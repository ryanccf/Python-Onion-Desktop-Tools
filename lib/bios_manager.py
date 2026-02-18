"""
bios_manager.py - Download, verify, cache, and install BIOS files for Onion OS.

Manages BIOS files required by various emulators on the Miyoo Mini/Mini+.
Downloads from the Abdess/retroarch_system GitHub repository, caches locally,
and installs to the SD card's BIOS/ directory.
"""

import hashlib
import logging
import shutil
from pathlib import Path
from typing import Callable, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CHUNK_SIZE = 64 * 1024  # 64 KiB
NETWORK_TIMEOUT = 60  # seconds (BIOS files can be large)

_BASE_RAW_URL = (
    "https://raw.githubusercontent.com/Abdess/retroarch_system/libretro/"
)

# Maps system name to the subdirectory path in the repo.
_SYSTEM_TO_REPO_PATH = {
    "PlayStation": "Sony - PlayStation/",
    "Neo Geo": "Arcade/",
    "Sega CD": "Sega - Mega CD - Sega CD/",
    "TurboGrafx-CD": "NEC - PC Engine - TurboGrafx 16 - SuperGrafx/",
    "Saturn": "Sega - Saturn/",
    "GBA": "Nintendo - Game Boy Advance/",
    "GB": "Nintendo - Gameboy/",
    "GBC": "Nintendo - Gameboy Color/",
    "Neo Geo CD": "SNK - NeoGeo CD/",
}

# ---------------------------------------------------------------------------
# BIOS file definitions
# ---------------------------------------------------------------------------

BIOS_FILES = [
    # --- PlayStation (required) ---
    {
        "filename": "scph1001.bin",
        "system": "PlayStation",
        "md5": "924e392ed05558ffdb115408c263dccf",
        "required": True,
        "subdir": "",
        "extra_copies": [],
        "notes": "PS1 BIOS (North America)",
    },
    {
        "filename": "scph5500.bin",
        "system": "PlayStation",
        "md5": "8dd7d5296a650fac7319bce665a6a53c",
        "required": True,
        "subdir": "",
        "extra_copies": [],
        "notes": "PS1 BIOS (Japan)",
    },
    {
        "filename": "scph5501.bin",
        "system": "PlayStation",
        "md5": "490f666e1afb15b7362b406ed1cea246",
        "required": True,
        "subdir": "",
        "extra_copies": [],
        "notes": "PS1 BIOS (North America)",
    },
    {
        "filename": "scph5502.bin",
        "system": "PlayStation",
        "md5": "32736f17079d0b2b7024407c39bd3050",
        "required": True,
        "subdir": "",
        "extra_copies": [],
        "notes": "PS1 BIOS (Europe)",
    },
    # --- Neo Geo (required) ---
    {
        "filename": "neogeo.zip",
        "system": "Neo Geo",
        "md5": "",
        "required": True,
        "subdir": "",
        "extra_copies": ["Roms/NEOGEO/neogeo.zip"],
        "notes": "Neo Geo BIOS (also needed in Roms/NEOGEO/)",
    },
    # --- Sega CD (required) ---
    {
        "filename": "bios_CD_U.bin",
        "system": "Sega CD",
        "md5": "2efd74e3232ff260e371b99f84024f7f",
        "required": True,
        "subdir": "",
        "extra_copies": [],
        "notes": "Sega CD BIOS (North America)",
    },
    {
        "filename": "bios_CD_E.bin",
        "system": "Sega CD",
        "md5": "e66fa1dc5820d254611fdcdba0662372",
        "required": True,
        "subdir": "",
        "extra_copies": [],
        "notes": "Sega CD BIOS (Europe)",
    },
    {
        "filename": "bios_CD_J.bin",
        "system": "Sega CD",
        "md5": "278a9397d192149e84e820ac621a8edd",
        "required": True,
        "subdir": "",
        "extra_copies": [],
        "notes": "Sega CD BIOS (Japan)",
    },
    # --- TurboGrafx-CD (required) ---
    {
        "filename": "syscard3.pce",
        "system": "TurboGrafx-CD",
        "md5": "38179df8f4ac870017db21ebcbf53114",
        "required": True,
        "subdir": "",
        "extra_copies": [],
        "notes": "TurboGrafx-CD / PC Engine CD System Card 3",
    },
    # --- Saturn (required) ---
    {
        "filename": "mpr-17933.bin",
        "system": "Saturn",
        "md5": "3240872c70984b6cbfda1586cab68dbe",
        "required": True,
        "subdir": "",
        "extra_copies": [],
        "notes": "Sega Saturn BIOS (Europe)",
    },
    # --- GBA (optional) ---
    {
        "filename": "gba_bios.bin",
        "system": "GBA",
        "md5": "a860e8c0b6d573d191e4ec7db1b1e4f6",
        "required": False,
        "subdir": "",
        "extra_copies": [],
        "notes": "Game Boy Advance BIOS (optional, HLE available)",
    },
    # --- GB / GBC (optional) ---
    {
        "filename": "gb_bios.bin",
        "system": "GB",
        "md5": "32fbbd84168d3482956eb3c5051637f5",
        "required": False,
        "subdir": "",
        "extra_copies": [],
        "notes": "Game Boy BIOS (optional)",
    },
    {
        "filename": "gbc_bios.bin",
        "system": "GBC",
        "md5": "dbfce9db9deaa2567f6a84fde55f9680",
        "required": False,
        "subdir": "",
        "extra_copies": [],
        "notes": "Game Boy Color BIOS (optional)",
    },
    # --- Neo Geo CD (optional) ---
    {
        "filename": "neocd_f.rom",
        "system": "Neo Geo CD",
        "md5": "",
        "required": False,
        "subdir": "neocd",
        "extra_copies": [],
        "notes": "Neo Geo CD front loader BIOS",
    },
    {
        "filename": "000-lo.lo",
        "system": "Neo Geo CD",
        "md5": "",
        "required": False,
        "subdir": "neocd",
        "extra_copies": [],
        "notes": "Neo Geo CD load order file",
    },
]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _build_download_url(bios_entry: dict) -> str:
    """Build the raw GitHub URL for a BIOS file."""
    system = bios_entry["system"]
    repo_path = _SYSTEM_TO_REPO_PATH.get(system, "")
    filename = bios_entry["filename"]
    # Percent-encode spaces and special chars in the path, but preserve slashes
    encoded_path = quote(f"{repo_path}{filename}", safe="/")
    return f"{_BASE_RAW_URL}{encoded_path}"


def _cache_path_for(bios_entry: dict, cache_dir: Path) -> Path:
    """Return the local cache path for a BIOS file, including subdirectory."""
    subdir = bios_entry.get("subdir", "")
    if subdir:
        return cache_dir / subdir / bios_entry["filename"]
    return cache_dir / bios_entry["filename"]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def verify_md5(file_path: Path, expected_md5: str) -> bool:
    """Verify the MD5 checksum of a file.

    Returns True if the checksum matches or if expected_md5 is empty (skip).
    """
    if not expected_md5:
        return True

    md5 = hashlib.md5()
    with open(file_path, "rb") as f:
        while True:
            chunk = f.read(CHUNK_SIZE)
            if not chunk:
                break
            md5.update(chunk)

    result = md5.hexdigest() == expected_md5.lower()
    if not result:
        logger.warning(
            "MD5 mismatch for %s: expected %s, got %s",
            file_path.name,
            expected_md5,
            md5.hexdigest(),
        )
    return result


def download_bios_file(
    bios_entry: dict,
    cache_dir: Path,
    progress_cb: Optional[Callable[[str, int, int], None]] = None,
) -> tuple[bool, str]:
    """Download a single BIOS file to the cache directory.

    Returns (success, message).
    """
    url = _build_download_url(bios_entry)
    dest = _cache_path_for(bios_entry, cache_dir)
    dest.parent.mkdir(parents=True, exist_ok=True)

    filename = bios_entry["filename"]
    logger.info("Downloading %s from %s", filename, url)

    try:
        request = Request(url)
        with urlopen(request, timeout=NETWORK_TIMEOUT) as response:
            total = int(response.headers.get("Content-Length", 0))
            downloaded = 0

            with open(dest, "wb") as fh:
                while True:
                    chunk = response.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    fh.write(chunk)
                    downloaded += len(chunk)
                    if progress_cb:
                        progress_cb(filename, downloaded, total)

    except HTTPError as exc:
        return False, f"HTTP {exc.code} downloading {filename}: {exc.reason}"
    except URLError as exc:
        return False, f"Network error downloading {filename}: {exc.reason}"
    except TimeoutError:
        return False, f"Timeout downloading {filename}"
    except OSError as exc:
        return False, f"File error saving {filename}: {exc}"

    # Verify MD5 if provided
    if not verify_md5(dest, bios_entry.get("md5", "")):
        dest.unlink(missing_ok=True)
        return False, f"MD5 verification failed for {filename}"

    logger.info("Downloaded and verified %s (%d bytes)", filename, downloaded)
    return True, f"Downloaded {filename}"


def scan_cached_bios(cache_dir: Path) -> dict[str, bool]:
    """Check which BIOS files exist in the local cache.

    Returns a dict mapping filename to whether it exists in cache.
    """
    result = {}
    for entry in BIOS_FILES:
        path = _cache_path_for(entry, cache_dir)
        result[entry["filename"]] = path.is_file()
    return result


def scan_sd_bios(sd_mount: Path) -> dict[str, bool]:
    """Check which BIOS files exist on the SD card.

    Returns a dict mapping filename to whether it exists in BIOS/ on the SD.
    """
    bios_dir = sd_mount / "BIOS"
    result = {}
    for entry in BIOS_FILES:
        subdir = entry.get("subdir", "")
        if subdir:
            path = bios_dir / subdir / entry["filename"]
        else:
            path = bios_dir / entry["filename"]
        result[entry["filename"]] = path.is_file()
    return result


def download_all_bios(
    cache_dir: Path,
    progress_cb: Optional[Callable[[float, str], None]] = None,
    skip_cached: bool = True,
    required_only: bool = False,
) -> tuple[bool, list[str], list[str]]:
    """Download all BIOS files to the cache directory.

    Parameters
    ----------
    cache_dir : Path
        Local directory to store downloaded files.
    progress_cb : callable, optional
        Called as progress_cb(fraction, status_text).
    skip_cached : bool
        If True, skip files that are already cached and pass MD5 verification.
    required_only : bool
        If True, only download required BIOS files.

    Returns
    -------
    tuple[bool, list[str], list[str]]
        (all_succeeded, succeeded_list, failed_list)
    """
    cache_dir.mkdir(parents=True, exist_ok=True)

    files = [e for e in BIOS_FILES if not required_only or e["required"]]
    total = len(files)
    succeeded = []
    failed = []

    for idx, entry in enumerate(files):
        filename = entry["filename"]
        frac = idx / max(total, 1)

        # Check cache
        if skip_cached:
            cached_path = _cache_path_for(entry, cache_dir)
            if cached_path.is_file() and verify_md5(cached_path, entry.get("md5", "")):
                if progress_cb:
                    progress_cb(frac, f"Cached: {filename}")
                succeeded.append(filename)
                logger.info("Skipping %s (already cached and verified)", filename)
                continue

        if progress_cb:
            progress_cb(frac, f"Downloading: {filename}")

        ok, msg = download_bios_file(entry, cache_dir)
        if ok:
            succeeded.append(filename)
        else:
            failed.append(f"{filename}: {msg}")
            logger.error("Failed to download %s: %s", filename, msg)

    if progress_cb:
        progress_cb(1.0, "Download complete")

    all_ok = len(failed) == 0
    return all_ok, succeeded, failed


def install_bios_to_sd(
    cache_dir: Path,
    sd_mount: Path,
    progress_cb: Optional[Callable[[float, str], None]] = None,
    required_only: bool = False,
) -> tuple[bool, list[str], list[str]]:
    """Copy cached BIOS files to the SD card's BIOS/ directory.

    Parameters
    ----------
    cache_dir : Path
        Local cache directory containing downloaded BIOS files.
    sd_mount : Path
        Root of the mounted SD card.
    progress_cb : callable, optional
        Called as progress_cb(fraction, status_text).
    required_only : bool
        If True, only install required BIOS files.

    Returns
    -------
    tuple[bool, list[str], list[str]]
        (all_succeeded, succeeded_list, failed_list)
    """
    bios_dir = sd_mount / "BIOS"
    bios_dir.mkdir(parents=True, exist_ok=True)

    files = [e for e in BIOS_FILES if not required_only or e["required"]]
    total = len(files)
    succeeded = []
    failed = []

    for idx, entry in enumerate(files):
        filename = entry["filename"]
        frac = idx / max(total, 1)

        src = _cache_path_for(entry, cache_dir)
        if not src.is_file():
            failed.append(f"{filename}: not in cache")
            logger.warning("Skipping %s (not in cache)", filename)
            continue

        if progress_cb:
            progress_cb(frac, f"Installing: {filename}")

        try:
            # Main copy to BIOS/ (with optional subdir)
            subdir = entry.get("subdir", "")
            if subdir:
                dest_dir = bios_dir / subdir
                dest_dir.mkdir(parents=True, exist_ok=True)
                dest = dest_dir / filename
            else:
                dest = bios_dir / filename

            shutil.copy2(src, dest)
            logger.info("Installed %s -> %s", filename, dest)

            # Extra copies (e.g. neogeo.zip -> Roms/NEOGEO/)
            for extra in entry.get("extra_copies", []):
                extra_dest = sd_mount / extra
                extra_dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, extra_dest)
                logger.info("Extra copy %s -> %s", filename, extra_dest)

            succeeded.append(filename)

        except OSError as exc:
            failed.append(f"{filename}: {exc}")
            logger.error("Failed to install %s: %s", filename, exc)

    if progress_cb:
        progress_cb(1.0, "Installation complete")

    all_ok = len(failed) == 0
    return all_ok, succeeded, failed
