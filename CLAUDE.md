# CLAUDE.md

Werkzeuge zum Import, zur Analyse und zur Transkription mit Sprechertrennung von Aufnahmen mit
dem **DJI Mic 2** (zwei Sender „Links“/„Rechts“, 32-Bit Float) und optional der Tonspur aus
Kamera-MOVs. Alles läuft lokal. Die Scripte müssen auf **macOS und Windows** funktionieren.

**Vor Änderungen [docs/LEARNINGS.md](docs/LEARNINGS.md) lesen.** Dort stehen Messwerte,
verworfene Ansätze und Fallstricke, z. B. warum Turbo statt large-v3, warum die Rückfallösung
mit Fenster ±2 s und warum der Kompressor vor der Verstärkung sitzt.

## Scripte

| Script | Zweck | Python |
|---|---|---|
| `dji_import.py` | kopiert DJI-Laufwerke „Links“/„Rechts“/„Stereo“ in gleichnamige Ordner | System |
| `audio_info.py` | technische Daten der Tonspuren (WAV/MOV), Prüfung des WAV-Headers, `--csv` | System |
| `mov_extract.py` | Stereo-WAV (PCM S16LE) aus MOV in denselben Ordner, `--split` für Mono | System |
| `transkript.py` | Paarbildung, Pegelanalyse, Whisper, Transkript `.txt/.srt/.json` | `.venv` |
| `anhoeren.py` | laute Hörkopien (−16 LUFS) nach `Anhoeren/` | System |

Voraussetzung für alle Scripte: FFmpeg/ffprobe im PATH. `transkript.py` braucht
`.venv` (`pip install -r requirements.txt`). Auf Apple Silicon nimmt es MLX (GPU), sonst
faster-whisper (CPU).

Einrichtung per `install_macos.command` bzw. `install_windows.bat` (Python, FFmpeg, `.venv`).
Startscripte je Script in `Start/` (`*.command` / `*.bat`, gemeinsam über `_start.sh`/`_start.bat`).

## Ordner

- `Links/`, `Rechts/`: DJI-Originale (32-Bit Float). **Nie verändern.**
- `Stereo/`: optional, MOV + daraus extrahierte Stereo-WAV.
- `Transkripte/`, `Anhoeren/`, `audio_info*.csv`: erzeugte Ausgaben.

## Konventionen

- Code, Kommentare und Ausgaben auf Deutsch. Umlaute im Code als ae/oe/ue, in Dokumenten normal.
- Kompatibel zu Python 3.9 bleiben (kein `X | None`, kein `match`). Prüfen mit
  `/usr/bin/python3 -m py_compile <datei>`.
- FFmpeg/ffprobe über `find_tool()` bzw. `find_ffprobe()` suchen. Die finden auch
  `/opt/homebrew/bin`, wenn die IDE einen anderen PATH hat.
- Ausgaben über eine temporäre Datei schreiben und dann umbenennen (keine halben Dateien).
- Für Tests von `transkript.py`: `--nur <Sitzung>`, `--nur-zuordnung`, `--erzwinge-rueckfall`,
  `--ausgabe <scratch>`. Referenzversätze stehen in LEARNINGS.md.
