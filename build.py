#!/usr/bin/env python3
"""
Build script for OnionInstaller.
Creates a standalone binary using PyInstaller for the current platform.
Output goes to releases/.

Usage:
    python3 build.py          # auto-creates a venv if needed
    .venv/bin/python build.py # use an existing venv
"""

import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent.resolve()
RELEASES_DIR = ROOT / "releases"
VENV_DIR = ROOT / ".venv"


def _in_venv():
    """Check if we're running inside a virtual environment."""
    return sys.prefix != sys.base_prefix


def _relaunch_in_venv():
    """Create a venv with system-site-packages (for gi) and re-exec this script."""
    print("Creating virtual environment with system site-packages...")
    subprocess.check_call([
        sys.executable, "-m", "venv",
        "--system-site-packages", str(VENV_DIR),
    ])
    if platform.system() == "Windows":
        venv_python = VENV_DIR / "Scripts" / "python.exe"
    else:
        venv_python = VENV_DIR / "bin" / "python3"
    print(f"Re-launching build inside venv: {venv_python}")
    os.execv(str(venv_python), [str(venv_python), __file__] + sys.argv[1:])


def ensure_pyinstaller():
    """Install PyInstaller if not available."""
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print("PyInstaller not found, installing...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "pyinstaller"])


def get_output_name():
    """Return the platform-specific binary name."""
    system = platform.system().lower()
    machine = platform.machine().lower()

    # Normalize architecture names
    if machine in ("x86_64", "amd64"):
        machine = "x86_64"
    elif machine in ("aarch64", "arm64"):
        machine = "arm64"

    if system == "windows":
        return f"OnionInstaller-windows-{machine}.exe"
    else:
        return f"OnionInstaller-{system}-{machine}"


def build():
    # If not in a venv, create one and re-exec so pip works on managed systems
    if not _in_venv():
        _relaunch_in_venv()

    ensure_pyinstaller()

    RELEASES_DIR.mkdir(exist_ok=True)

    output_name = get_output_name()
    separator = ";" if platform.system() == "Windows" else ":"

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--onefile",
        "--name", output_name,
        "--noconfirm",
        "--clean",
    ]

    # Application icon
    icon_path = ROOT / "icon.png"
    if icon_path.exists():
        if platform.system() == "Windows":
            # Convert PNG to ICO for Windows
            try:
                from PIL import Image
                ico_path = ROOT / "icon.ico"
                img = Image.open(icon_path)
                img.save(ico_path, format="ICO", sizes=[(16, 16), (32, 32), (48, 48), (256, 256)])
                cmd += ["--icon", str(ico_path)]
            except ImportError:
                print("Warning: Pillow not available, skipping Windows icon")
        else:
            cmd += ["--icon", str(icon_path)]

    # Bundle data files
    if (ROOT / "config.json").exists():
        cmd += ["--add-data", f"config.json{separator}."]
    if (ROOT / "resources").is_dir():
        cmd += ["--add-data", f"resources{separator}resources"]
    if icon_path.exists():
        cmd += ["--add-data", f"icon.png{separator}."]

    # Hidden imports for PyGObject/GTK3
    for module in [
        "gi",
        "gi.repository.Gtk",
        "gi.repository.Gdk",
        "gi.repository.GLib",
        "gi.repository.Pango",
        "gi.repository.GdkPixbuf",
    ]:
        cmd += ["--hidden-import", module]

    cmd.append("main.py")

    print(f"Building {output_name}...")
    print(f"Command: {' '.join(cmd)}")
    subprocess.check_call(cmd, cwd=str(ROOT))

    # Move binary from dist/ to releases/
    built = ROOT / "dist" / output_name
    dest = RELEASES_DIR / output_name

    if dest.exists():
        dest.unlink()
    shutil.move(str(built), str(dest))

    # Clean up build artifacts
    for d in (ROOT / "build", ROOT / "dist"):
        if d.exists():
            shutil.rmtree(d)
    spec_file = ROOT / f"{output_name}.spec"
    if spec_file.exists():
        spec_file.unlink()

    print(f"Build complete: {dest}")
    print(f"Size: {dest.stat().st_size / (1024 * 1024):.1f} MB")


if __name__ == "__main__":
    build()
