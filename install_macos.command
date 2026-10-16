#!/bin/bash
# Einrichtung fuer macOS: Python 3.9+ und FFmpeg (falls nicht vorhanden),
# dann .venv anlegen und requirements.txt installieren.
# Start per Doppelklick im Finder oder im Terminal: ./install_macos.command

set -e
cd "$(dirname "$0")"
PROJEKT="$(pwd)"
PY_PKG_URL="https://www.python.org/ftp/python/3.12.10/python-3.12.10-macos11.pkg"

export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"

python_ok() {
    "$1" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' >/dev/null 2>&1
}

finde_python() {
    for kandidat in python3 /opt/homebrew/bin/python3 /usr/local/bin/python3 \
                    /Library/Frameworks/Python.framework/Versions/Current/bin/python3; do
        if command -v "$kandidat" >/dev/null 2>&1 && python_ok "$kandidat"; then
            # /usr/bin/python3 ist ohne Command Line Tools nur ein Platzhalter
            if [ "$(command -v "$kandidat")" = "/usr/bin/python3" ] && ! xcode-select -p >/dev/null 2>&1; then
                continue
            fi
            command -v "$kandidat"
            return 0
        fi
    done
    return 1
}

echo "== Python suchen"
PY="$(finde_python || true)"
if [ -z "$PY" ]; then
    if command -v brew >/dev/null 2>&1; then
        echo "Python nicht gefunden, installiere mit Homebrew ..."
        brew install python
    else
        echo "Python nicht gefunden, lade Installer von python.org ..."
        TMP_PKG="$(mktemp -d)/python.pkg"
        curl -fL "$PY_PKG_URL" -o "$TMP_PKG"
        echo "Installation braucht das Administrator-Passwort:"
        sudo installer -pkg "$TMP_PKG" -target /
        rm -f "$TMP_PKG"
    fi
    PY="$(finde_python || true)"
    if [ -z "$PY" ]; then
        echo "FEHLER: Python 3.9+ konnte nicht installiert werden." >&2
        exit 1
    fi
fi
echo "Python: $PY ($("$PY" --version 2>&1))"

echo "== FFmpeg suchen"
if command -v ffmpeg >/dev/null 2>&1 && command -v ffprobe >/dev/null 2>&1; then
    echo "FFmpeg: $(command -v ffmpeg)"
elif command -v brew >/dev/null 2>&1; then
    echo "FFmpeg nicht gefunden, installiere mit Homebrew ..."
    brew install ffmpeg
else
    echo "WARNUNG: FFmpeg fehlt und Homebrew ist nicht installiert."
    echo "         Homebrew: https://brew.sh, danach: brew install ffmpeg"
fi

echo "== Virtuelle Umgebung .venv"
if [ ! -x "$PROJEKT/.venv/bin/python" ]; then
    "$PY" -m venv "$PROJEKT/.venv"
fi
"$PROJEKT/.venv/bin/python" -m pip install --upgrade pip
"$PROJEKT/.venv/bin/python" -m pip install -r "$PROJEKT/requirements.txt"

chmod +x "$PROJEKT"/Start/*.command 2>/dev/null || true
echo
echo "Fertig. Startscripte liegen in Start/ (Doppelklick auf *.command)."
