#!/usr/bin/env python3
"""
Extrahiert die Stereo-Tonspur aus MOV-Dateien als Stereo-WAV in denselben Ordner:

    A001_10051547_C007.MOV -> A001_10051547_C007.wav

Mit --split werden die Kanaele zusaetzlich in zwei Mono-WAVs aufgeteilt:

    linker Kanal  -> <Clip>_Links.wav   (Sender "Links" / TX1 des DJI Mic 2)
    rechter Kanal -> <Clip>_Rechts.wav  (Sender "Rechts" / TX2)

Zielformat: WAV, PCM signed 16 Bit little-endian (pcm_s16le), Abtastrate wie im Original.
Ist die Tonspur AAC (verlustbehaftet), wird sie decodiert und als PCM gespeichert.
Die verlorene Qualitaet laesst sich dadurch nicht zurueckholen, aber alle Exporte
haben dasselbe Format und lassen sich direkt mit den DJI-Einzeldateien vergleichen.

Aufnahmedatum, -uhrzeit und Timecode der Kamera werden als BWF-Metadaten (bext-Chunk)
in die WAVs geschrieben. Programme wie Audition, Reaper oder DaVinci Resolve koennen
die Spuren damit zeitlich einordnen.

Alles laeuft lokal mit FFmpeg:
    macOS:   brew install ffmpeg
    Windows: winget install Gyan.FFmpeg

Aufruf:
    python3 mov_extract.py                       # alle MOVs in ./Stereo -> Stereo-WAV daneben
    python3 mov_extract.py clip.mov --ziel Export
    python3 mov_extract.py --split               # zusaetzlich je Kanal eine Mono-WAV
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
CHANNELS = (("Links", "c0"), ("Rechts", "c1"))


def find_tool(name):
    path = shutil.which(name)
    if path:
        return path
    for prefix in ("/opt/homebrew/bin", "/usr/local/bin"):
        candidate = os.path.join(prefix, name)
        if os.path.exists(candidate):
            return candidate
    return None


def probe(ffprobe, path):
    cmd = [ffprobe, "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)]
    out = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", check=True).stdout
    return json.loads(out)


def bwf_metadata(info, sample_rate):
    """Baut BWF-Felder aus Aufnahmezeit und Timecode der Kamera."""
    tags = info.get("format", {}).get("tags", {})
    meta = {}
    created = tags.get("com.apple.quicktime.creationdate") or tags.get("creation_time")
    if created:
        try:
            dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
            meta["origination_date"] = dt.strftime("%Y-%m-%d")
            meta["origination_time"] = dt.strftime("%H:%M:%S")
        except ValueError:
            pass

    timecode = next((s["tags"]["timecode"] for s in info.get("streams", [])
                     if s.get("tags", {}).get("timecode")), None)
    if timecode:
        h, m, s = (int(x) for x in timecode.replace(";", ":").split(":")[:3])
        # time_reference = Samples seit Mitternacht (BWF-Standard)
        meta["time_reference"] = str((h * 3600 + m * 60 + s) * sample_rate)
        meta["comment"] = f"Timecode {timecode}"
    meta["originator"] = "mov_extract.py"
    return meta


def run_ffmpeg(ffmpeg, src, stream_index, pan, dst, meta):
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
           "-i", str(src), "-map", f"0:{stream_index}", "-vn",
           "-af", pan, "-c:a", "pcm_s16le",
           "-write_bext", "1", "-map_metadata", "-1"]
    for key, value in meta.items():
        cmd += ["-metadata", f"{key}={value}"]
    cmd.append(str(dst))
    subprocess.run(cmd, check=True)


def extract(ffmpeg, ffprobe, src, target, split):
    info = probe(ffprobe, src)
    audio = [s for s in info.get("streams", []) if s.get("codec_type") == "audio"]
    if not audio:
        print("  Keine Tonspur gefunden – uebersprungen.")
        return []
    stream = audio[0]
    codec = stream.get("codec_name")
    rate = int(stream.get("sample_rate") or 48000)
    channels = int(stream.get("channels") or 0)
    print(f"  Quelle: {codec}, {rate} Hz, {channels} Kanaele")
    if codec and not codec.startswith("pcm_"):
        print(f"  Hinweis: {codec} ist verlustbehaftet – wird decodiert und als PCM S16LE gespeichert.")
    if channels != 2:
        print(f"  Hinweis: erwartet 2 Kanaele, gefunden {channels}.")

    target.mkdir(parents=True, exist_ok=True)
    meta = bwf_metadata(info, rate)
    written = []

    if channels >= 2:
        jobs = [(f"{src.stem}.wav", "pan=stereo|c0=c0|c1=c1")]
        if split:
            jobs += [(f"{src.stem}_{label}.wav", f"pan=mono|c0={ch}") for label, ch in CHANNELS]
    else:
        jobs = [(f"{src.stem}.wav", "pan=mono|c0=c0")]

    for name, pan in jobs:
        dst = target / name
        run_ffmpeg(ffmpeg, src, stream["index"], pan, dst, meta)
        print(f"  -> {dst}")
        written.append(dst)
    return written


def collect(paths):
    files = []
    for p in paths:
        if p.is_dir():
            files += sorted(f for f in p.iterdir()
                            if f.suffix.lower() in (".mov", ".mp4") and not f.name.startswith("."))
        elif p.is_file():
            files.append(p)
        else:
            print(f"Nicht gefunden: {p}", file=sys.stderr)
    return files


def main():
    parser = argparse.ArgumentParser(description="Ton aus MOV als Stereo-WAV (PCM S16LE) exportieren")
    parser.add_argument("pfade", nargs="*", type=Path, help="MOV-Dateien oder Ordner (Standard: ./Stereo)")
    parser.add_argument("--ziel", type=Path, help="Ausgabeordner (Standard: Ordner der MOV-Datei)")
    parser.add_argument("--split", action="store_true", help="zusaetzlich je Kanal eine Mono-WAV schreiben")
    args = parser.parse_args()

    ffmpeg, ffprobe = find_tool("ffmpeg"), find_tool("ffprobe")
    if not ffmpeg or not ffprobe:
        print("FFmpeg nicht gefunden. Installieren mit:\n"
              "  macOS:   brew install ffmpeg\n"
              "  Windows: winget install Gyan.FFmpeg", file=sys.stderr)
        return 1

    files = collect(args.pfade or [SCRIPT_DIR / "Stereo"])
    if not files:
        print("Keine MOV-Dateien gefunden.")
        return 1

    errors = 0
    for src in files:
        print(f"[{src.name}]")
        target = args.ziel or src.parent
        try:
            extract(ffmpeg, ffprobe, src, target, args.split)
        except subprocess.CalledProcessError as exc:
            print(f"  FEHLER: {exc}", file=sys.stderr)
            errors += 1
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
