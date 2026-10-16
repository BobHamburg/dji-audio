#!/usr/bin/env python3
"""
Erzeugt laute Hoerkopien der DJI-Aufnahmen zum bequemen Anhoeren.

Die 32-Bit-Float-Aufnahmen des DJI Mic 2 haben viel Reserve nach oben und sind deshalb
im normalen Player sehr leise (Sprache um -30 LUFS). Dieses Script hebt sie auf eine
uebliche Sprachlautheit an (Standard -16 LUFS, wie bei Podcasts). Ein sanfter Kompressor
glaettet die kurzen Spitzen der Ansteckmikros, ein Limiter begrenzt auf -1 dBTP, damit
nichts verzerrt. Gemessen wird
nach EBU R128; nach dem Anheben wird nachgemessen und bei Bedarf einmal korrigiert,
weil der Limiter die Lautheit etwas senkt.

Die Originale bleiben unveraendert. Die Kopien landen in einem eigenen Ordner:

    Links/DJI_03_....WAV  ->  Anhoeren/Links/DJI_03_....wav
    Rechts/DJI_04_....WAV ->  Anhoeren/Rechts/DJI_04_....wav

Bereits erzeugte Kopien werden uebersprungen, solange das Original nicht neuer ist.

Voraussetzung: FFmpeg (macOS: brew install ffmpeg, Windows: winget install Gyan.FFmpeg)

Aufruf:
    python3 anhoeren.py                      # Links/ und Rechts/ -> Anhoeren/
    python3 anhoeren.py --format m4a         # AAC statt WAV (kleiner)
    python3 anhoeren.py --lautheit -14       # lauter (Streaming-Niveau)
    python3 anhoeren.py --neu                # vorhandene Kopien neu erzeugen
    python3 anhoeren.py Stereo               # andere Ordner verarbeiten
"""

import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_FOLDERS = ("Links", "Rechts")
FORMATS = {
    "wav": (["-c:a", "pcm_s16le"], ".wav"),
    "wav24": (["-c:a", "pcm_s24le"], ".wav"),
    "m4a": (["-c:a", "aac", "-b:a", "192k"], ".m4a"),
}


def find_tool(name):
    path = shutil.which(name)
    if path:
        return path
    for prefix in ("/opt/homebrew/bin", "/usr/local/bin"):
        candidate = os.path.join(prefix, name)
        if os.path.exists(candidate):
            return candidate
    sys.exit(f"{name} nicht gefunden. Bitte FFmpeg installieren "
             "(macOS: brew install ffmpeg, Windows: winget install Gyan.FFmpeg).")


def measure(ffmpeg, path):
    """Integrierte Lautheit (LUFS) und True Peak (dBTP) nach EBU R128."""
    cmd = [ffmpeg, "-hide_banner", "-nostats", "-i", str(path), "-af", "ebur128=peak=true",
           "-f", "null", "-"]
    err = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace").stderr
    summary = err[err.rfind("Summary:"):]
    loud = re.search(r"I:\s+(-?[\d.]+|-inf) LUFS", summary)
    peak = re.search(r"Peak:\s+(-?[\d.]+|-inf) dBFS", summary)
    if not loud or not peak:
        raise RuntimeError("Messung fehlgeschlagen:\n" + err[-500:])
    return float(loud.group(1)), float(peak.group(1))


def render(ffmpeg, src, dst, loud_in, gain_db, peak_db, codec_args, rate):
    """1. Kompressor auf dem Originalpegel: Ansteckmikros liefern Spitzen (Plosive, Lachen,
          Klatschen) bis 20 dB ueber der Sprachlautheit. Die Schwelle liegt 10 dB ueber der
          gemessenen Lautheit und haengt damit nicht von der Verstaerkung ab.
       2. Verstaerkung auf die Ziel-Lautheit.
       3. Limiter gegen Uebersteuerung (1 dB Reserve fuer True Peak)."""
    threshold = 10 ** ((loud_in + 10) / 20)
    limit = 10 ** ((peak_db - 1.0) / 20)
    filt = (f"acompressor=threshold={threshold:.6f}:ratio=3:attack=3:release=120:knee=4,"
            f"volume={gain_db:.2f}dB,"
            f"aresample={rate * 4},"     # Limiter mit 4-facher Abtastrate: erfasst auch Spitzen
            f"alimiter=limit={limit:.4f}:attack=1:release=30:level=false,"   # zwischen den Samples
            f"aresample={rate}")
    tmp = dst.with_name(dst.stem + ".part" + dst.suffix)
    cmd = [ffmpeg, "-hide_banner", "-nostats", "-y", "-i", str(src), "-af", filt,
           "-ar", str(rate), *codec_args, "-map_metadata", "0", str(tmp)]
    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if result.returncode != 0:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(result.stderr[-500:])
    os.replace(tmp, dst)


def sample_rate(ffprobe, path):
    out = subprocess.run([ffprobe, "-v", "error", "-select_streams", "a:0", "-show_entries",
                          "stream=sample_rate", "-of", "csv=p=0", str(path)],
                         capture_output=True, text=True).stdout.strip()
    return int(out or 48000)


def collect(folders):
    files = []
    for folder in folders:
        if not folder.is_dir():
            print(f"Ordner nicht gefunden, uebersprungen: {folder}")
            continue
        files += sorted(f for f in folder.iterdir()
                        if f.suffix.lower() == ".wav" and not f.name.startswith("."))
    return files


def main():
    parser = argparse.ArgumentParser(description="Laute Hoerkopien der DJI-Aufnahmen erzeugen")
    parser.add_argument("ordner", nargs="*", type=Path,
                        help="Quellordner (Standard: Links und Rechts im Script-Ordner)")
    parser.add_argument("--ziel", type=Path, default=SCRIPT_DIR / "Anhoeren", help="Ausgabeordner")
    parser.add_argument("--lautheit", type=float, default=-16.0, help="Ziel-Lautheit in LUFS (Standard -16)")
    parser.add_argument("--spitze", type=float, default=-1.0, help="max. Spitzenpegel in dBTP (Standard -1)")
    parser.add_argument("--format", choices=FORMATS, default="wav",
                        help="wav (16 Bit), wav24 (24 Bit) oder m4a (AAC 192 kbit/s)")
    parser.add_argument("--neu", action="store_true", help="vorhandene Kopien neu erzeugen")
    args = parser.parse_args()

    ffmpeg, ffprobe = find_tool("ffmpeg"), find_tool("ffprobe")
    folders = args.ordner or [SCRIPT_DIR / f for f in DEFAULT_FOLDERS]
    codec_args, ext = FORMATS[args.format]

    files = collect(folders)
    if not files:
        print("Keine WAV-Dateien gefunden.")
        return 1

    done = skipped = failed = 0
    for src in files:
        dst = args.ziel / src.parent.name / (src.stem + ext)
        label = f"{src.parent.name}/{src.name}"
        if src.stat().st_size <= 1024:
            print(f"[{label}] leer, uebersprungen")
            skipped += 1
            continue
        if not args.neu and dst.exists() and dst.stat().st_mtime >= src.stat().st_mtime:
            print(f"[{label}] Kopie vorhanden, uebersprungen")
            skipped += 1
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        try:
            loud_in, peak_in = measure(ffmpeg, src)
            if loud_in == float("-inf"):
                print(f"[{label}] nur Stille, uebersprungen")
                skipped += 1
                continue
            rate = sample_rate(ffprobe, src)
            gain = args.lautheit - loud_in
            render(ffmpeg, src, dst, loud_in, gain, args.spitze, codec_args, rate)
            loud_out, peak_out = measure(ffmpeg, dst)
            for _ in range(4):   # Kompressor/Limiter senken die Lautheit: nachkorrigieren
                if abs(loud_out - args.lautheit) <= 0.5:
                    break
                gain += args.lautheit - loud_out
                render(ffmpeg, src, dst, loud_in, gain, args.spitze, codec_args, rate)
                loud_out, peak_out = measure(ffmpeg, dst)
        except Exception as exc:  # noqa: BLE001 – Fehler je Datei melden, weitermachen
            print(f"[{label}] FEHLER: {exc}", file=sys.stderr)
            failed += 1
            continue
        print(f"[{label}] {loud_in:6.1f} -> {loud_out:6.1f} LUFS (+{gain:.1f} dB), "
              f"Spitze {peak_in:+5.1f} -> {peak_out:+5.1f} dBTP")
        done += 1

    print(f"\n{done} Kopie(n) erzeugt, {skipped} uebersprungen, {failed} Fehler. Ziel: {args.ziel}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
