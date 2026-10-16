#!/usr/bin/env python3
"""
Zeigt die technischen Eigenschaften der Tonspuren in WAV- und MOV-Dateien an:
Codec, Komprimierung, Abtastrate, Bittiefe (Abtasttiefe), Kanaele, Bitrate,
Dauer und Timecode bzw. Aufnahmezeit (wichtig zum Synchronisieren der Spuren).

WAV-Dateien werden zusaetzlich direkt im RIFF-Header geprueft. So fallen leere
oder abgebrochene Aufnahmen auf, etwa wenn dem Sender der Akku ausging.

Voraussetzung: ffprobe (Teil von FFmpeg) muss installiert und im PATH sein.
    macOS:   brew install ffmpeg
    Windows: winget install Gyan.FFmpeg

Aufruf:
    python3 audio_info.py                      # alle Dateien unterhalb des Script-Ordners
    python3 audio_info.py Links Stereo/x.mov   # bestimmte Ordner/Dateien
    python3 audio_info.py --json bericht.json  # zusaetzlich als JSON speichern
    python3 audio_info.py --csv bericht.csv    # zusaetzlich als CSV (eine Zeile je Tonspur)
"""

import argparse
import csv
import json
import os
import shutil
import struct
import subprocess
import sys
from pathlib import Path

MEDIA_EXTENSIONS = {".wav", ".mov", ".mp4", ".m4a"}

# WAVE-Format-Tags aus dem fmt-Chunk
WAVE_FORMAT_TAGS = {
    0x0001: "PCM (Ganzzahl)",
    0x0003: "IEEE Float",
    0x0006: "A-law",
    0x0007: "mu-law",
    0xFFFE: "Extensible",
}

LOSSLESS_CODECS = {"flac", "alac", "wavpack", "tta", "mlp", "truehd", "ape"}

SAMPLE_FMT_BITS = {
    "u8": 8, "u8p": 8, "s16": 16, "s16p": 16, "s32": 32, "s32p": 32,
    "s64": 64, "s64p": 64, "flt": 32, "fltp": 32, "dbl": 64, "dblp": 64,
}


# --------------------------------------------------------------------------- ffprobe

def find_ffprobe():
    path = shutil.which("ffprobe")
    if path:
        return path
    for candidate in ("/opt/homebrew/bin/ffprobe", "/usr/local/bin/ffprobe"):
        if os.path.exists(candidate):
            return candidate
    return None


def run_ffprobe(ffprobe, path):
    cmd = [ffprobe, "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)]
    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "ffprobe-Fehler")
    return json.loads(result.stdout)


# --------------------------------------------------------------------------- WAV-Header

def read_wav_header(path):
    """Liest die RIFF-Chunks direkt aus der Datei und prueft sie auf Plausibilitaet."""
    info = {"chunks": [], "warnings": []}
    file_size = path.stat().st_size
    with open(path, "rb") as f:
        head = f.read(12)
        if len(head) < 12 or head[8:12] != b"WAVE" or head[:4] not in (b"RIFF", b"RF64", b"BW64"):
            info["warnings"].append("Kein gueltiger RIFF/WAVE-Header")
            return info
        info["container"] = head[:4].decode()
        riff_size = struct.unpack("<I", head[4:8])[0]
        if head[:4] == b"RIFF" and riff_size + 8 != file_size:
            info["warnings"].append(
                f"RIFF-Groesse ({riff_size + 8} B) passt nicht zur Dateigroesse ({file_size} B)")

        while True:
            hdr = f.read(8)
            if len(hdr) < 8:
                break
            cid, size = struct.unpack("<4sI", hdr)
            cid = cid.decode("latin-1")
            pos = f.tell()
            info["chunks"].append(f"{cid.strip()}({size})")
            if cid == "fmt ":
                fmt = f.read(min(size, 40))
                tag, ch, rate, byte_rate, align, bits = struct.unpack("<HHIIHH", fmt[:16])
                if tag == 0xFFFE and len(fmt) >= 26:
                    tag = struct.unpack("<H", fmt[24:26])[0]  # SubFormat-GUID beginnt mit dem echten Tag
                    info["extensible"] = True
                info.update(format_tag=tag, format_name=WAVE_FORMAT_TAGS.get(tag, f"0x{tag:04X}"),
                            channels=ch, sample_rate=rate, byte_rate=byte_rate,
                            block_align=align, bits=bits)
            elif cid == "data":
                info["data_size"] = size
                available = file_size - pos
                if size == 0:
                    info["warnings"].append("data-Chunk ist leer – Datei enthaelt keine Audiodaten")
                elif size > available:
                    info["warnings"].append(
                        f"data-Chunk meldet {size} B, vorhanden sind nur {available} B (abgeschnitten)")
            f.seek(pos + size + (size & 1))

    if "data_size" in info and info.get("byte_rate"):
        info["duration"] = info["data_size"] / info["byte_rate"]
    return info


# --------------------------------------------------------------------------- Auswertung

def to_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def compression_of(codec):
    if codec.startswith("pcm_"):
        return "unkomprimiert (PCM)"
    if codec in LOSSLESS_CODECS:
        return "verlustfrei komprimiert"
    return "verlustbehaftet komprimiert"


def bit_depth_of(stream):
    codec = stream.get("codec_name", "")
    bits = int(stream.get("bits_per_raw_sample") or 0) or int(stream.get("bits_per_sample") or 0)
    if codec.startswith("pcm_"):
        kind = "Float" if codec.startswith("pcm_f") else "Integer"
        return f"{bits} Bit {kind}" if bits else kind
    if codec in LOSSLESS_CODECS and bits:
        return f"{bits} Bit"
    # AAC, MP3, Opus ... speichern keine Samples, sondern Spektraldaten
    decoded = SAMPLE_FMT_BITS.get(stream.get("sample_fmt", ""))
    hint = f", decodiert als {decoded} Bit {'Float' if 'f' in stream.get('sample_fmt', '') else 'Int'}" \
        if decoded else ""
    return f"– (keine feste Bittiefe{hint})"


def analyze(path, ffprobe):
    result = {"datei": str(path), "groesse_mb": round(path.stat().st_size / 1024 / 1024, 2),
              "audio": [], "video": [], "warnungen": []}

    wav = read_wav_header(path) if path.suffix.lower() == ".wav" else None
    if wav:
        result["wav_header"] = wav
        result["warnungen"].extend(wav["warnings"])

    try:
        probe = run_ffprobe(ffprobe, path)
    except Exception as exc:  # noqa: BLE001 – Fehler pro Datei melden, nicht abbrechen
        result["warnungen"].append(f"ffprobe: {exc}")
        return result

    fmt = probe.get("format", {})
    tags = fmt.get("tags", {})
    result["container"] = fmt.get("format_long_name", fmt.get("format_name"))
    result["dauer_s"] = to_float(fmt.get("duration")) or (wav or {}).get("duration")
    result["aufnahmezeit"] = (tags.get("com.apple.quicktime.creationdate")
                              or tags.get("creation_time")
                              or " ".join(filter(None, [tags.get("date"), tags.get("creation_time")])) or None)
    result["geraet"] = " / ".join(filter(None, [tags.get("com.apple.quicktime.model"),
                                                tags.get("com.apple.quicktime.software"),
                                                tags.get("encoded_by"), tags.get("encoder")])) or None

    for s in probe.get("streams", []):
        stags = s.get("tags", {})
        if stags.get("timecode") and not result.get("timecode"):
            result["timecode"] = stags["timecode"]
        if s.get("codec_type") == "video":
            fps = s.get("avg_frame_rate", "0/0")
            num, _, den = fps.partition("/")
            fps_val = round(float(num) / float(den), 3) if den and float(den) else None
            result["video"].append({
                "codec": s.get("codec_name"), "profil": s.get("profile"),
                "aufloesung": f"{s.get('width')}x{s.get('height')}", "fps": fps_val,
                "bitrate_kbps": round(to_float(s.get("bit_rate")) / 1000) if s.get("bit_rate") else None,
            })
        elif s.get("codec_type") == "audio":
            codec = s.get("codec_name", "?")
            rate = int(s.get("sample_rate") or 0)
            channels = int(s.get("channels") or 0)
            bitrate = to_float(s.get("bit_rate"))
            if bitrate is None and codec.startswith("pcm_"):
                bitrate = rate * channels * int(s.get("bits_per_sample") or 0)
            result["audio"].append({
                "index": s.get("index"),
                "codec": codec,
                "codec_name": s.get("codec_long_name"),
                "profil": s.get("profile") if s.get("profile") not in (None, "unknown") else None,
                "komprimierung": compression_of(codec),
                "abtastrate_hz": rate,
                "bittiefe": bit_depth_of(s),
                "kanaele": channels,
                "kanallayout": s.get("channel_layout") or None,
                "bitrate_kbps": round(bitrate / 1000, 1) if bitrate else None,
                "dauer_s": to_float(s.get("duration")) or result["dauer_s"],
                "sprache": stags.get("language") if stags.get("language") not in (None, "und") else None,
            })

    if not result["audio"]:
        result["warnungen"].append("Keine Tonspur gefunden")
    return result


# --------------------------------------------------------------------------- Ausgabe

def fmt_duration(seconds):
    if seconds is None:
        return "?"
    m, s = divmod(seconds, 60)
    h, m = divmod(int(m), 60)
    return f"{h:d}:{m:02d}:{s:06.3f}"


def print_report(r):
    print("=" * 78)
    print(f"{r['datei']}   ({r['groesse_mb']} MB)")
    if r.get("container"):
        print(f"  Container:    {r['container']}")
    print(f"  Dauer:        {fmt_duration(r.get('dauer_s'))}")
    for key, label in (("aufnahmezeit", "Aufnahmezeit"), ("timecode", "Timecode"), ("geraet", "Geraet")):
        if r.get(key):
            print(f"  {label + ':':<14}{r[key]}")
    wav = r.get("wav_header")
    if wav and "format_name" in wav:
        ext = " (WAVE_FORMAT_EXTENSIBLE)" if wav.get("extensible") else ""
        print(f"  WAV-Header:   {wav.get('container')}, {wav['format_name']}{ext}, "
              f"Chunks: {' '.join(wav['chunks'])}")

    for v in r["video"]:
        print(f"  Video:        {v['codec']} {v['profil'] or ''}, {v['aufloesung']}, "
              f"{v['fps']} fps, {v['bitrate_kbps']} kbit/s")

    for a in r["audio"]:
        print(f"  Tonspur #{a['index']}:")
        print(f"    Codec:          {a['codec']} – {a['codec_name']}"
              + (f" (Profil {a['profil']})" if a["profil"] else ""))
        print(f"    Komprimierung:  {a['komprimierung']}")
        print(f"    Abtastrate:     {a['abtastrate_hz'] / 1000:g} kHz")
        print(f"    Bittiefe:       {a['bittiefe']}")
        layout = f" ({a['kanallayout']})" if a["kanallayout"] else ""
        print(f"    Kanaele:        {a['kanaele']}{layout}")
        print(f"    Bitrate:        {a['bitrate_kbps']} kbit/s" if a["bitrate_kbps"] else
              "    Bitrate:        ?")
        if a["sprache"]:
            print(f"    Sprache:        {a['sprache']}")

    for w in r["warnungen"]:
        print(f"  ! WARNUNG: {w}")


def write_csv(results, path):
    fields = ["datei", "dauer_s", "aufnahmezeit", "timecode", "index", "codec", "profil",
              "komprimierung", "abtastrate_hz", "bittiefe", "kanaele", "kanallayout",
              "bitrate_kbps", "warnungen"]
    with open(path, "w", newline="", encoding="utf-8-sig") as f:  # BOM, damit Excel Umlaute erkennt
        writer = csv.DictWriter(f, fieldnames=fields, delimiter=";", extrasaction="ignore")
        writer.writeheader()
        for r in results:
            base = {k: r.get(k) for k in ("datei", "dauer_s", "aufnahmezeit", "timecode")}
            base["warnungen"] = " | ".join(r["warnungen"])
            for a in r["audio"] or [{}]:
                writer.writerow({**base, **a})


# --------------------------------------------------------------------------- main

def collect_files(paths):
    files = []
    for p in paths:
        if p.is_file():
            files.append(p)
        elif p.is_dir():
            for dirpath, dirnames, filenames in os.walk(p):
                dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
                files.extend(Path(dirpath) / n for n in sorted(filenames)
                             if not n.startswith(".") and Path(n).suffix.lower() in MEDIA_EXTENSIONS)
        else:
            print(f"Nicht gefunden: {p}", file=sys.stderr)
    return files


def main():
    parser = argparse.ArgumentParser(description="Tonspuren in WAV/MOV-Dateien analysieren")
    parser.add_argument("pfade", nargs="*", type=Path,
                        help="Dateien oder Ordner (Standard: Ordner des Scripts)")
    parser.add_argument("--json", type=Path, help="Ergebnis zusaetzlich als JSON speichern")
    parser.add_argument("--csv", type=Path, help="Ergebnis zusaetzlich als CSV speichern")
    args = parser.parse_args()

    ffprobe = find_ffprobe()
    if not ffprobe:
        print("ffprobe nicht gefunden. Bitte FFmpeg installieren:\n"
              "  macOS:   brew install ffmpeg\n"
              "  Windows: winget install Gyan.FFmpeg", file=sys.stderr)
        return 1

    files = collect_files(args.pfade or [Path(__file__).resolve().parent])
    if not files:
        print("Keine WAV/MOV-Dateien gefunden.")
        return 1

    results = [analyze(f, ffprobe) for f in files]
    for r in results:
        print_report(r)

    warned = [r for r in results if r["warnungen"]]
    print("=" * 78)
    print(f"{len(results)} Datei(en) analysiert, {len(warned)} mit Warnungen.")

    if args.json:
        args.json.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"JSON gespeichert: {args.json}")
    if args.csv:
        write_csv(results, args.csv)
        print(f"CSV gespeichert:  {args.csv}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
