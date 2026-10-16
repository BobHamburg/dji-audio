#!/usr/bin/env python3
"""
Importiert Aufnahmen von DJI-Mic-2-Sendern auf einen lokalen Datentraeger.

Gesucht werden die Laufwerke mit dem Namen "Links", "Rechts" und "Stereo".
Gefunden werden sie unter macOS in /Volumes, unter Windows ueber das Laufwerkslabel
und unter Linux in /media bzw. /run/media. Die Audiodateien jedes gefundenen
Laufwerks werden in einen gleichnamigen lokalen Ordner kopiert:

    <Ziel>/Links/DJI_Audio_001/DJI_01_20250711_221653.WAV
    <Ziel>/Rechts/...
    <Ziel>/Stereo/...

Dateien, die bereits mit gleicher Groesse im Ziel liegen, werden uebersprungen.
Dadurch kann das Script beliebig oft laufen und kopiert nur neue Aufnahmen.

Aufruf:
    python3 dji_import.py                 # Ziel = Ordner, in dem das Script liegt
    python3 dji_import.py --ziel D:\\Audio
    python3 dji_import.py --dry-run       # nur anzeigen, nichts kopieren
"""

import argparse
import os
import platform
import shutil
import string
import sys
from pathlib import Path

VOLUME_NAMES = ("Links", "Rechts", "Stereo")
AUDIO_EXTENSIONS = {".wav", ".mp3", ".aac", ".m4a"}
CHUNK_SIZE = 8 * 1024 * 1024


def find_volume_windows(label):
    import ctypes

    kernel32 = ctypes.windll.kernel32
    bitmask = kernel32.GetLogicalDrives()
    for i, letter in enumerate(string.ascii_uppercase):
        if not bitmask & (1 << i):
            continue
        root = f"{letter}:\\"
        name_buf = ctypes.create_unicode_buffer(261)
        fs_buf = ctypes.create_unicode_buffer(261)
        ok = kernel32.GetVolumeInformationW(
            ctypes.c_wchar_p(root), name_buf, len(name_buf),
            None, None, None, fs_buf, len(fs_buf),
        )
        if ok and name_buf.value.lower() == label.lower():
            return Path(root)
    return None


def find_volume_unix(label):
    system = platform.system()
    if system == "Darwin":
        candidates = [Path("/Volumes") / label]
    else:
        user = os.environ.get("USER", "")
        candidates = [
            Path("/media") / user / label,
            Path("/run/media") / user / label,
            Path("/media") / label,
            Path("/mnt") / label,
        ]
    for path in candidates:
        if path.is_dir():
            return path
    return None


def find_volume(label):
    if platform.system() == "Windows":
        return find_volume_windows(label)
    return find_volume_unix(label)


def is_hidden(path, root):
    # Versteckte Dateien/Ordner (.Spotlight-V100, .Trashes, ._*-Dateien von macOS, ...)
    return any(part.startswith(".") for part in path.relative_to(root).parts)


def iter_audio_files(root):
    for dirpath, dirnames, filenames in os.walk(root):
        current = Path(dirpath)
        # Versteckte Ordner gar nicht erst betreten
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for name in sorted(filenames):
            path = current / name
            if is_hidden(path, root):
                continue
            if path.suffix.lower() in AUDIO_EXTENSIONS:
                yield path


def copy_file(src, dst):
    """Kopiert ueber eine temporaere Datei, damit abgebrochene Kopien nicht als fertig gelten."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(dst.name + ".part")
    with open(src, "rb") as fin, open(tmp, "wb") as fout:
        shutil.copyfileobj(fin, fout, CHUNK_SIZE)
    shutil.copystat(src, tmp)  # Zeitstempel der Aufnahme behalten
    os.replace(tmp, dst)


def import_volume(label, source, target_root, dry_run):
    target = target_root / label
    copied = skipped = 0
    copied_bytes = 0

    files = list(iter_audio_files(source))
    if not files:
        print(f"  Keine Audiodateien gefunden.")
        return 0, 0

    for src in files:
        rel = src.relative_to(source)
        dst = target / rel
        size = src.stat().st_size
        if dst.exists() and dst.stat().st_size == size:
            skipped += 1
            continue
        print(f"  {rel}  ({size / 1024 / 1024:.1f} MB)")
        if not dry_run:
            copy_file(src, dst)
        copied += 1
        copied_bytes += size

    action = "wuerden kopiert" if dry_run else "kopiert"
    print(f"  -> {copied} Datei(en) {action} ({copied_bytes / 1024 / 1024:.1f} MB), "
          f"{skipped} bereits vorhanden. Ziel: {target}")
    return copied, skipped


def main():
    parser = argparse.ArgumentParser(description="DJI Mic 2 Aufnahmen importieren")
    parser.add_argument(
        "--ziel", type=Path, default=Path(__file__).resolve().parent,
        help="Zielverzeichnis (Standard: Ordner des Scripts)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Nur anzeigen, nichts kopieren")
    args = parser.parse_args()

    target_root = args.ziel.expanduser().resolve()
    found_any = False

    for label in VOLUME_NAMES:
        source = find_volume(label)
        if source is None:
            print(f"[{label}] nicht angeschlossen.")
            continue
        found_any = True
        print(f"[{label}] gefunden: {source}")
        try:
            import_volume(label, source, target_root, args.dry_run)
        except OSError as exc:
            print(f"  FEHLER: {exc}", file=sys.stderr)

    if not found_any:
        print("Kein DJI-Laufwerk (Links/Rechts/Stereo) gefunden.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
