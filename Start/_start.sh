#!/bin/bash
# Gemeinsamer Starter fuer macOS: _start.sh <script.py> [Argumente ...]
# Das Arbeitsverzeichnis bleibt, relative Pfade in Argumenten gelten also wie gewohnt.
# Nimmt .venv/bin/python, falls vorhanden, sonst python3 aus dem System.

PROJEKT="$(cd "$(dirname "$0")/.." && pwd)"
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
SCRIPT="$1"
shift

if [ -x "$PROJEKT/.venv/bin/python" ]; then
    PY="$PROJEKT/.venv/bin/python"
elif [ "$SCRIPT" = "transkript.py" ]; then
    echo "FEHLER: .venv fehlt. Bitte zuerst install_macos.command ausfuehren." >&2
    exit 1
else
    PY="python3"
fi

"$PY" "$PROJEKT/$SCRIPT" "$@"
