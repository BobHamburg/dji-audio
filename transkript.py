#!/usr/bin/env python3
"""
Transkription mit Sprecherzuordnung ueber zwei Mikrofone (DJI Mic 2, Sender Links/Rechts).

Prinzip
-------
Jeder Sprecher traegt einen eigenen Sender. Sprecher 1 ist auf "Links" laut und auf
"Rechts" leise, Sprecher 2 umgekehrt. Das Programm

  1. findet zusammengehoerige Aufnahmen in ./Links und ./Rechts (Kreuzkorrelation),
  2. legt sie samplegenau uebereinander,
  3. misst fuer jeden Zeitabschnitt (20 ms) den Pegelunterschied Links - Rechts im
     Sprachband und kalibriert die unterschiedliche Verstaerkung der Sender heraus,
  4. transkribiert beide Kanaele lokal mit Whisper und behaelt von
     jedem Kanal nur die Woerter, bei denen dieser Kanal dominiert. Uebersprechen des
     anderen Sprechers fliegt damit heraus, gleichzeitiges Sprechen bleibt erhalten.

32-Bit-Float-Aufnahmen werden ohne Umwandlung in Float verarbeitet, die Dynamik bleibt
also vollstaendig erhalten (auch Pegel ueber 0 dBFS).

Optional: Liegt in ./Stereo eine passende Stereo-WAV (z. B. aus mov_extract.py), werden
die Zeitstempel zusaetzlich auf die Zeitachse dieses Videos umgerechnet, so passt die
SRT-Datei direkt zum Video. Gibt es zu einer Stereo-WAV keine DJI-Aufnahmen, werden
ihr linker und rechter Kanal als Sprecher 1/2 verwendet.

Ausgabe in ./Transkripte:
    <Name>.txt   lesbares Transkript, nach Sprechern getrennt
    <Name>.srt   Untertitel
    <Name>.json  alle Woerter mit Zeit, Sprecher und Pegeldifferenz

Installation (einmalig, im Projektordner):
    python3 -m venv .venv
    .venv/bin/pip install -r requirements.txt          (Windows: .venv\\Scripts\\pip ...)
Das Whisper-Modell wird beim ersten Lauf einmalig heruntergeladen, danach laeuft alles offline.

JSpracherkennung (--engine):
    Apple Silicon (M1/M2/M3...): MLX auf der GPU, Modell mlx-community/whisper-large-v3-turbo
                                 (etwa 6x schneller als auf der CPU)
    Windows, Linux, Intel-Mac:   faster-whisper auf der CPU

Aufruf:
    .venv/bin/python transkript.py
    .venv/bin/python transkript.py --sprecher1 Robert --sprecher2 Anna
    .venv/bin/python transkript.py --nur-zuordnung        # nur Paare + Pegel, ohne Whisper
    .venv/bin/python transkript.py --modell small         # schneller, ungenauer
    .venv/bin/python transkript.py --engine faster-whisper  # CPU statt GPU erzwingen
"""

import argparse
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

import numpy as np

os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")   # keine Download-Balken mitten in der Ausgabe

SCRIPT_DIR = Path(__file__).resolve().parent
SR = 16000                 # Abtastrate fuer Analyse und Whisper
HOP = 320                  # 20 ms Analyseraster
NFFT = 1024
BAND = (150, 4000)         # Sprachband fuer die Pegelmessung in Hz
CORR_SR = 8000             # Abtastrate fuer die Kreuzkorrelation
MATCH_RATIO = 4.0          # Korrelations-Peak muss so viel staerker sein als der naechstbeste
FALLBACK_MIN_R = 0.3       # Mindestkorrelation der Pegelverlaeufe fuer die Rueckfallloesung
FALLBACK_MIN_OVERLAP = 3.0 # Aufnahmen muessen sich laut Dateinamen mind. so viele s ueberlappen
DJI_NAME = re.compile(r"DJI_\d+_(\d{8})_(\d{6})", re.I)


# =========================================================================== Audio

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


FFMPEG = None
FFPROBE = None


def channel_count(path):
    out = subprocess.run([FFPROBE, "-v", "error", "-select_streams", "a:0", "-show_entries",
                          "stream=channels", "-of", "csv=p=0", str(path)],
                         capture_output=True, text=True).stdout.strip()
    return int(out or 0)


def load(path, channel=0, rate=SR):
    """Laedt einen Kanal als float32 – ohne Normalisierung, Pegel > 1.0 bleiben erhalten."""
    cmd = [FFMPEG, "-v", "error", "-i", str(path), "-af", f"pan=mono|c0=c{channel}",
           "-ar", str(rate), "-f", "f32le", "-"]
    data = subprocess.run(cmd, capture_output=True, check=True).stdout
    return np.frombuffer(data, np.float32).copy()


def xcorr(a, b, rate, center=None, window=None, phat=False):
    """Versatz von b in a in Sekunden (positiv: b beginnt spaeter als a) und Peak-Verhaeltnis
    (Hauptpeak zu naechstbestem Peak). Optional nur im Suchfenster center +- window.
    phat=True normiert das Spektrum (GCC-PHAT) – robuster bei verschiedenen Mikrofonen."""
    a = a - a.mean()
    b = b - b.mean()
    n = len(a) + len(b) - 1
    size = 1 << (n - 1).bit_length()
    spec = np.fft.rfft(a, size) * np.conj(np.fft.rfft(b, size))
    if phat:
        spec /= np.abs(spec) + 1e-12
    c = np.abs(np.fft.irfft(spec, size))
    c = np.concatenate([c[-(len(b) - 1):], c[:len(a)]])
    lags = (np.arange(len(c)) - (len(b) - 1)) / rate
    if center is not None:
        mask = np.abs(lags - center) <= window
        if mask.sum() < rate:
            return 0.0, 0.0
        c, lags = c[mask], lags[mask]
    k = int(np.argmax(c))
    peak, lag = c[k], lags[k]
    c = c.copy()
    c[max(0, k - rate // 2):k + rate // 2] = 0
    return float(lag), float(peak / (c.max() + 1e-20))


def level_curve(x, rate, step=0.01):
    """Pegelverlauf in dB in 10-ms-Schritten, Stille/Rauschen abgeschnitten."""
    w = int(rate * step)
    n = len(x) // w
    e = 10 * np.log10((x[:n * w].astype(np.float64) ** 2).reshape(n, w).mean(1) + 1e-12)
    return np.maximum(e, np.percentile(e, 20)) if n else e


def align_by_level(a, b, rate, expected, window, step=0.01):
    """Rueckfall: Versatz von b in a ueber die Korrelation der Pegelverlaeufe, gesucht nur
    im Fenster expected +- window (expected aus den Zeitstempeln der Dateinamen).
    Beide Mikrofone hoeren, wann ueberhaupt gesprochen wird – dieser Verlauf ist auch dann
    aehnlich, wenn die Wellenformen der beiden Mikrofone kaum korrelieren."""
    ea, eb = level_curve(a, rate, step), level_curve(b, rate, step)
    best_r, best_lag = -1.0, 0
    for lag in range(int(round((expected - window) / step)), int(round((expected + window) / step)) + 1):
        a0, b0 = max(lag, 0), max(-lag, 0)
        n = min(len(ea) - a0, len(eb) - b0)
        if n < 100:   # weniger als 1 s Ueberlappung
            continue
        x = ea[a0:a0 + n] - ea[a0:a0 + n].mean()
        y = eb[b0:b0 + n] - eb[b0:b0 + n].mean()
        corr = float(np.dot(x, y) / np.sqrt(np.dot(x, x) * np.dot(y, y) + 1e-20))
        if corr > best_r:
            best_r, best_lag = corr, lag
    return best_lag * step, best_r


def place(x, offset_s, length):
    """Legt Signal x mit Versatz offset_s auf eine Zeitachse der Laenge length (Samples, SR)."""
    out = np.zeros(length, np.float32)
    k = int(round(offset_s * SR))
    src0, dst0 = max(0, -k), max(0, k)
    n = min(len(x) - src0, length - dst0)
    if n > 0:
        out[dst0:dst0 + n] = x[src0:src0 + n]
    return out


# =========================================================================== Dateien und Paare

@dataclass
class Session:
    name: str
    left: Path
    right: Path
    left_ch: int = 0
    right_ch: int = 0
    right_offset: float = 0.0          # Rechts beginnt so viele s nach Links
    match_ratio: float = 0.0
    video: Optional[Path] = None          # optionale Stereo-WAV als Referenz-Zeitachse
    video_offset: float = 0.0          # Links beginnt so viele s nach Beginn der Stereo-WAV
    start_clock: Optional[datetime] = None
    notes: list = field(default_factory=list)


def dji_time(path):
    m = DJI_NAME.search(path.name)
    return datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S") if m else None


def wav_files(folder):
    if not folder.is_dir():
        return []
    return sorted(f for f in folder.iterdir()
                  if f.suffix.lower() == ".wav" and not f.name.startswith(".") and f.stat().st_size > 1024)


def find_sessions(base, max_gap_min, clock_window, fallback_window=2.0, force_fallback=False):
    lefts, rights, stereos = wav_files(base / "Links"), wav_files(base / "Rechts"), wav_files(base / "Stereo")
    stereos = [s for s in stereos if channel_count(s) >= 2]
    cache = {}

    def low(path, ch=0):
        if (path, ch) not in cache:
            cache[(path, ch)] = load(path, ch, CORR_SR)
        return cache[(path, ch)]

    # 1) Jede DJI-Datei in den Stereo-WAVs suchen. Der Empfaenger zeichnet dasselbe Signal auf
    #    wie der Sender, daher ist diese Zuordnung sehr zuverlaessig.
    in_stereo = {}   # DJI-Datei -> (Stereo-WAV, Versatz in der Stereo-WAV)
    for f in ([] if force_fallback else lefts + rights):
        best = None
        for st in stereos:
            for ch in (0, 1):
                offset, ratio = xcorr(low(st, ch), low(f), CORR_SR)
                if ratio >= MATCH_RATIO and (best is None or ratio > best[0]):
                    best = (ratio, st, offset)
        if best:
            in_stereo[f] = (best[1], best[2])
            print(f"  {f.parent.name}/{f.name} liegt in {best[1].name} bei {best[2]:+.3f} s "
                  f"(Peak-Verhaeltnis {best[0]:.1f})")

    # 2) Links/Rechts-Paare
    candidates = []
    for lf in lefts:
        for rf in rights:
            if lf in in_stereo and rf in in_stereo and in_stereo[lf][0] == in_stereo[rf][0]:
                offset = in_stereo[rf][1] - in_stereo[lf][1]
                candidates.append((100.0, lf, rf, offset, "ueber Stereo-WAV"))
                continue
            tl, tr = dji_time(lf), dji_time(rf)
            if not (tl and tr):
                continue
            expected = (tr - tl).total_seconds()
            if abs(expected) > max_gap_min * 60:
                continue
            # a) direkt korrelieren, aber nur nahe dem Versatz laut Dateinamen
            if not force_fallback:
                best = max((xcorr(low(lf), low(rf), CORR_SR, expected, clock_window, phat)
                            for phat in (False, True)), key=lambda x: x[1])
                if best[1] >= MATCH_RATIO:
                    candidates.append((best[1], lf, rf, best[0], f"direkt, Peak-Verhaeltnis {best[1]:.1f}"))
                    continue
            # b) Rueckfall: Versatz aus den Dateinamen, Feinabgleich ueber den Pegelverlauf.
            #    Nur fuer Aufnahmen, die sich laut Dateinamen zeitlich ueberlappen.
            dur_l, dur_r = len(low(lf)) / CORR_SR, len(low(rf)) / CORR_SR
            overlap = min(dur_l, expected + dur_r) - max(0.0, expected)
            if overlap < FALLBACK_MIN_OVERLAP:
                continue
            offset, corr = align_by_level(low(lf), low(rf), CORR_SR, expected, fallback_window)
            print(f"  Rueckfall {lf.name} <-> {rf.name}: Dateinamen {expected:+.0f} s, "
                  f"Pegelverlauf {offset:+.3f} s (r = {corr:.2f})")
            if corr >= FALLBACK_MIN_R:
                candidates.append((corr, lf, rf, offset,
                                   f"Rueckfall: Dateiname + Pegelverlauf, r = {corr:.2f}"))

    sessions, used = [], set()
    for ratio, lf, rf, offset, how in sorted(candidates, key=lambda c: -c[0]):
        if lf in used or rf in used:
            continue
        used |= {lf, rf}
        s = Session(name=lf.stem, left=lf, right=rf, right_offset=offset, match_ratio=ratio,
                    start_clock=dji_time(lf))
        print(f"  Paar: Links/{lf.name} + Rechts/{rf.name}, Rechts ab {offset:+.3f} s ({how})")
        if how.startswith("Rueckfall"):
            s.notes.append(f"Versatz ueber Rueckfallloesung bestimmt ({how}). Genauigkeit ca. 10 ms.")
        if lf in in_stereo:
            s.video, s.video_offset = in_stereo[lf]
            s.name = s.video.stem
        sessions.append(s)

    for f in lefts + rights:
        if f not in used:
            print(f"  ohne Partner, uebersprungen: {f.parent.name}/{f.name}")

    # 3) Stereo-WAVs ohne DJI-Paar: ihre beiden Kanaele direkt verwenden
    used_stereo = {s.video for s in sessions}
    for st in ([] if force_fallback else stereos):
        if st not in used_stereo:
            s = Session(name=st.stem, left=st, right=st, left_ch=0, right_ch=1, video=st)
            s.notes.append("Keine DJI-Einzelaufnahmen gefunden – Kanaele der Stereo-WAV verwendet.")
            sessions.append(s)

    # Namen eindeutig machen (mehrere Paare koennen in derselben Stereo-WAV liegen)
    seen = {}
    for s in sessions:
        seen[s.name] = seen.get(s.name, 0) + 1
        if seen[s.name] > 1:
            s.name = f"{s.name}_{seen[s.name]}"
    return sessions


# =========================================================================== Pegelanalyse

def band_level_db(x):
    """Pegel im Sprachband je 20-ms-Rahmen in dB."""
    window = np.hanning(NFFT).astype(np.float32)
    pad = np.pad(x, (NFFT // 2, NFFT // 2))
    frames = 1 + (len(pad) - NFFT) // HOP
    idx = np.arange(NFFT)[None, :] + HOP * np.arange(frames)[:, None]
    lo, hi = (int(f * NFFT / SR) for f in BAND)
    levels = np.empty(frames, np.float32)
    for i in range(0, frames, 4096):  # blockweise, damit lange Dateien wenig Speicher brauchen
        spec = np.abs(np.fft.rfft(pad[idx[i:i + 4096]] * window, axis=1)) ** 2
        levels[i:i + 4096] = spec[:, lo:hi].sum(axis=1)
    return 10 * np.log10(levels + 1e-12)


def smooth(x, n):
    if n <= 1:
        return x
    return np.convolve(x, np.ones(n) / n, mode="same")


def two_means(d):
    """1D-k-Means mit zwei Gruppen: (oberes Zentrum, unteres Zentrum)."""
    hi, lo = np.percentile(d, 85), np.percentile(d, 15)
    for _ in range(30):
        g = np.abs(d - hi) < np.abs(d - lo)
        if g.all() or (~g).all():
            break
        hi, lo = d[g].mean(), d[~g].mean()
    return float(hi), float(lo)


@dataclass
class Levels:
    rel: np.ndarray        # kalibrierte Dominanz in dB je Rahmen: + = Sprecher 1, - = Sprecher 2
    active: np.ndarray     # Rahmen mit Sprache
    loud: np.ndarray       # lautester Kanal in dB (Gewichtung)
    both: np.ndarray       # beide Mikrofone nehmen auf
    info: dict


def frame_mask(start_s, end_s, n):
    m = np.zeros(n, bool)
    m[max(0, int(start_s * SR / HOP)):max(0, int(end_s * SR / HOP))] = True
    return m


def analyze_levels(left, right, span_l, span_r, manual_offset=None):
    """span_l/span_r: (Beginn, Ende) in s, in denen der jeweilige Sender tatsaechlich aufnimmt."""
    lv_l, lv_r = band_level_db(left), band_level_db(right)
    n = min(len(lv_l), len(lv_r))
    lv_l, lv_r = lv_l[:n], lv_r[:n]
    valid_l, valid_r = frame_mask(*span_l, n), frame_mask(*span_r, n)
    both = valid_l & valid_r

    # Aktivitaet: deutlich ueber dem Grundrauschen des jeweiligen Kanals
    floor_l = np.percentile(lv_l[valid_l], 10) if valid_l.any() else 0.0
    floor_r = np.percentile(lv_r[valid_r], 10) if valid_r.any() else 0.0
    act_l = valid_l & (lv_l > floor_l + 12)
    act_r = valid_r & (lv_r > floor_r + 12)
    active = act_l | act_r
    diff = lv_l - lv_r

    info = {"rauschen_links_db": round(float(floor_l), 1), "rauschen_rechts_db": round(float(floor_r), 1),
            "sprachanteil": round(float(active[valid_l | valid_r].mean()), 3),
            "gemeinsam_s": round(float(both.sum() * HOP / SR), 1)}

    # --- Bereich, in dem beide Sender aufnehmen: Pegeldifferenz Links - Rechts
    d = diff[active & both]
    if manual_offset is not None:
        center = manual_offset
        info["kalibrierung"] = "manuell"
    elif len(d) >= 50:
        hi, lo = two_means(d)
        center = (hi + lo) / 2
        info["kalibrierung"] = "automatisch"
        info["sprecher1_db"], info["sprecher2_db"] = round(hi - center, 1), round(lo - center, 1)
        info["trennung_db"] = round(hi - lo, 1)
        if hi - lo < 6:
            info["warnung"] = ("Pegelunterschied zwischen den Sprechern gering – Zuordnung unsicher "
                               "(evtl. spricht nur eine Person oder die Mikrofone hingen dicht beieinander).")
    else:
        center = 0.0
        info["kalibrierung"] = "keine (zu wenig gemeinsame Sprache)"
    info["mitte_db"] = round(float(center), 1)
    rel = np.where(both, diff - center, 0.0)

    # --- Bereiche mit nur einem Sender: eigene Stimme (laut) vs. Uebersprechen (leise)
    for lv, valid, act, sign, label in ((lv_l, valid_l, act_l, 1, "links"), (lv_r, valid_r, act_r, -1, "rechts")):
        single = valid & ~both
        if not single.any():
            continue
        own = sign * rel > 3
        other = sign * rel < -3
        if (both & act & own).sum() >= 50 and (both & act & other).sum() >= 50:
            thr = (np.median(lv[both & act & own]) + np.median(lv[both & act & other])) / 2
            how = "aus gemeinsamem Bereich gelernt"
        elif (single & act).sum() >= 50:
            hi, lo = two_means(lv[single & act])
            thr = (hi + lo) / 2 if hi - lo >= 6 else lo - 1   # keine 2 Gruppen: alles eigene Stimme
            how = "aus Einzelkanal geschaetzt"
        else:
            continue
        rel = np.where(single, sign * (lv - thr), rel)
        info[f"nur_{label}_s"] = round(float(single.sum() * HOP / SR), 1)
        info[f"nur_{label}_schwelle"] = how

    rel = smooth(rel, 10)  # 200 ms glaetten
    return Levels(rel.astype(np.float32), active, np.maximum(lv_l, lv_r), both, info)


def word_dominance(lev: "Levels", t0: float, t1: float):
    """Kalibrierte Pegeldifferenz (dB, + = Sprecher 1) ueber das Wortintervall,
    ob dort Sprache erkannt wurde und ob beide Sender aufnehmen."""
    a = max(0, int(t0 * SR / HOP))
    b = min(len(lev.rel), max(int(t1 * SR / HOP), a + 1))
    if a >= b:
        return 0.0, False, False
    if not lev.active[a:b].any():
        # Wort-Zeitstempel von Whisper liegen v. a. beim ersten Wort eines Satzes oft etwas
        # daneben (in der Stille davor) – dann +-0,3 s um das Wort herum suchen
        pad = int(0.3 * SR / HOP)
        a, b = max(0, a - pad), min(len(lev.rel), b + pad)
    seg_active = lev.active[a:b]
    both = bool(lev.both[a:b].mean() > 0.5)
    if not seg_active.any():
        return float(lev.rel[a:b].mean()), False, both
    weights = np.power(10, (lev.loud[a:b] - lev.loud[a:b].max()) / 10) * seg_active
    return float(np.average(lev.rel[a:b], weights=weights + 1e-20)), True, both


# =========================================================================== Whisper

MLX_MODELS = {
    "large-v3-turbo": "mlx-community/whisper-large-v3-turbo",
    "large-v3": "mlx-community/whisper-large-v3-mlx",
}


def mlx_available():
    if sys.platform != "darwin" or platform.machine() != "arm64":
        return False
    try:
        import mlx_whisper  # noqa: F401
        return True
    except ImportError:
        return False


class WhisperEngine:
    """Spracherkennung ueber MLX (GPU, Apple Silicon) oder faster-whisper (CPU, alle Systeme)."""

    def __init__(self, engine, model_name):
        if engine == "auto":
            engine = "mlx" if mlx_available() else "faster-whisper"
        self.engine = engine
        if engine == "mlx":
            import mlx_whisper
            self._mlx = mlx_whisper
            self.model = model_name if "/" in model_name else \
                MLX_MODELS.get(model_name, f"mlx-community/whisper-{model_name}-mlx")
        else:
            from faster_whisper import WhisperModel
            self.model = model_name
            self._fw = WhisperModel(model_name, device="auto", compute_type="int8",
                                    cpu_threads=os.cpu_count() or 4)

    def describe(self):
        where = "GPU (MLX)" if self.engine == "mlx" else "CPU (faster-whisper)"
        return f"{self.model} auf {where}"

    def transcribe(self, audio, language, span):
        """audio: float32 mit 16 kHz; span: (Beginn, Ende) in s, in denen der Kanal Daten hat."""
        if self.engine == "mlx":
            result = self._mlx.transcribe(
                audio, path_or_hf_repo=self.model, language=language, word_timestamps=True,
                condition_on_previous_text=False, verbose=None,
                clip_timestamps=f"{span[0]:.2f},{span[1]:.2f}",   # stille Raender ueberspringen
            )
            return [{"start": float(w["start"]), "end": float(w["end"]), "text": w["word"],
                     "prob": float(w["probability"])}
                    for seg in result["segments"] for w in seg.get("words", [])]
        segments, _ = self._fw.transcribe(
            audio, language=language, word_timestamps=True, vad_filter=True,
            condition_on_previous_text=False, beam_size=5,
        )
        return [{"start": float(w.start), "end": float(w.end), "text": w.word, "prob": float(w.probability)}
                for seg in segments for w in seg.words or []]


def transcribe(engine, audio, language, label, span):
    peak = float(np.max(np.abs(audio))) or 1.0
    normed = (audio / peak * 0.9).astype(np.float32)   # nur fuer Whisper, Analyse nutzt Originalpegel
    print(f"    Whisper: Kanal {label} ({(span[1] - span[0]) / 60:.1f} min) ...", end=" ", flush=True)
    t0 = time.perf_counter()
    words = engine.transcribe(normed, language, span)
    print(f"{time.perf_counter() - t0:.0f} s", flush=True)
    return words


def norm_word(text):
    t = unicodedata.normalize("NFKC", text).lower()
    return re.sub(r"[^\w]", "", t)


def assign_words(words_l, words_r, lev: "Levels", margin: float):
    kept = []
    for side, words in ((1, words_l), (2, words_r)):
        for w in words:
            dom, is_active, both = word_dominance(lev, w["start"], w["end"])
            if not is_active:
                continue
            if both:
                # beide Mikrofone: nur Woerter behalten, bei denen der eigene Kanal dominiert
                own = dom if side == 1 else -dom
                if own <= -margin:
                    continue
                speaker = side
            else:
                # nur ein Mikrofon: laut = Traeger des Senders, leise = Uebersprechen des anderen
                speaker = 1 if dom > 0 else 2
            kept.append({**w, "sprecher": speaker, "pegeldiff_db": round(dom, 1),
                         "quelle": "Links" if side == 1 else "Rechts",
                         "einkanalig": not both})

    # Doppelte Woerter (von beiden Kanaelen erkannt) nur beim dominanten Kanal behalten
    kept.sort(key=lambda w: w["start"])
    drop = set()
    for i, a in enumerate(kept):
        for j in range(i + 1, len(kept)):
            b = kept[j]
            if b["start"] > a["end"]:
                break
            if a["quelle"] == b["quelle"] or norm_word(a["text"]) != norm_word(b["text"]):
                continue
            overlap = min(a["end"], b["end"]) - max(a["start"], b["start"])
            if overlap > 0.5 * min(a["end"] - a["start"], b["end"] - b["start"]):
                own_a = a["pegeldiff_db"] if a["quelle"] == "Links" else -a["pegeldiff_db"]
                own_b = b["pegeldiff_db"] if b["quelle"] == "Links" else -b["pegeldiff_db"]
                drop.add(j if own_a >= own_b else i)
    return [w for k, w in enumerate(kept) if k not in drop]


def smooth_speakers(words, clear_db=8.0, max_words=2, max_gap=0.4):
    """Kurze Einschuebe (max. 2 Woerter) mitten im Satz des anderen Sprechers diesem zuschlagen,
    sofern ihr Pegelunterschied nicht eindeutig ist. Echte Zwischenrufe ("Ja", "Hm") haben
    meist einen deutlichen Pegelunterschied und bleiben erhalten."""
    words = sorted(words, key=lambda w: w["start"])
    changed = True
    while changed:
        changed = False
        i = 0
        while i < len(words):
            j = i
            while j + 1 < len(words) and words[j + 1]["sprecher"] == words[i]["sprecher"]:
                j += 1
            if 0 < i and j + 1 < len(words) and j - i + 1 <= max_words:
                prev, nxt = words[i - 1], words[j + 1]
                run = words[i:j + 1]
                if (prev["sprecher"] == nxt["sprecher"] != run[0]["sprecher"]
                        and run[0]["start"] - prev["end"] <= max_gap
                        and nxt["start"] - run[-1]["end"] <= max_gap
                        and all(abs(w["pegeldiff_db"]) < clear_db for w in run)):
                    for w in run:
                        w["sprecher"] = prev["sprecher"]
                        w["geglaettet"] = True
                    changed = True
            i = j + 1
    return words


def build_turns(words, max_gap=1.5):
    turns = []
    for w in words:
        t = turns[-1] if turns else None
        if t and t["sprecher"] == w["sprecher"] and w["start"] - t["end"] <= max_gap:
            t["end"] = max(t["end"], w["end"])
            t["text"] += w["text"]
            t["einkanalig"] = t["einkanalig"] and w.get("einkanalig", False)
        else:
            turns.append({"sprecher": w["sprecher"], "start": w["start"], "end": w["end"], "text": w["text"],
                          "einkanalig": w.get("einkanalig", False)})
    for t in turns:
        t["text"] = re.sub(r"\s+", " ", t["text"]).strip()
    return [t for t in turns if t["text"]]


# =========================================================================== Ausgabe

def ts(seconds, srt=False):
    sign = "-" if seconds < 0 else ""
    seconds = abs(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if srt:
        return f"{int(h):02d}:{int(m):02d}:{int(s):02d},{int(round((s % 1) * 1000)) % 1000:03d}"
    return f"{sign}{int(h):02d}:{int(m):02d}:{s:04.1f}"


def describe_levels(info, names):
    out = []
    if "trennung_db" in info:
        out.append(f"Pegeltrennung {info['trennung_db']:.1f} dB ({names[1]} {info['sprecher1_db']:+.1f} dB, "
                   f"{names[2]} {info['sprecher2_db']:+.1f} dB um die Mitte {info['mitte_db']:+.1f} dB), "
                   f"Kalibrierung {info['kalibrierung']}, gemeinsam aufgenommen {info['gemeinsam_s']:.1f} s")
    else:
        out.append(f"Kalibrierung {info['kalibrierung']}, gemeinsam aufgenommen {info['gemeinsam_s']:.1f} s")
    for label, name in (("links", "Links"), ("rechts", "Rechts")):
        if f"nur_{label}_s" in info:
            out.append(f"nur {name} nimmt auf: {info[f'nur_{label}_s']:.1f} s "
                       f"(Trennung ueber Lautstaerke, Schwelle {info[f'nur_{label}_schwelle']})")
    return out


def write_outputs(session, turns, words, info, names, out_dir, timeline_note):
    out_dir.mkdir(parents=True, exist_ok=True)
    base = out_dir / session.name
    same = session.left == session.right

    def rel_path(p):
        try:
            return p.relative_to(SCRIPT_DIR)
        except ValueError:
            return p

    lines = [f"Transkript: {session.name}",
             f"{names[1]} (Mikrofon Links):  {rel_path(session.left)}" + (" [Kanal 1]" if same else ""),
             f"{names[2]} (Mikrofon Rechts): {rel_path(session.right)}" + (" [Kanal 2]" if same else ""),
             f"Zeitachse: {timeline_note}"]
    lines += describe_levels(info, names)
    lines += [f"Hinweis: {n}" for n in session.notes]
    if info.get("warnung"):
        lines.append(f"WARNUNG: {info['warnung']}")
    lines.append("")
    for t in turns:
        mark = "  (nur ein Mikrofon)" if t.get("einkanalig") else ""
        lines.append(f"[{ts(t['start'])} - {ts(t['end'])}] {names[t['sprecher']]}:{mark}")
        lines.append(f"    {t['text']}")
        lines.append("")
    base.with_suffix(".txt").write_text("\n".join(lines), encoding="utf-8")

    srt, i = [], 0
    for t in turns:
        if t["end"] <= 0:          # liegt vor dem Videostart
            continue
        i += 1
        srt += [str(i), f"{ts(max(0.0, t['start']), True)} --> {ts(t['end'], True)}",
                f"{names[t['sprecher']]}: {t['text']}", ""]
    base.with_suffix(".srt").write_text("\n".join(srt), encoding="utf-8")

    data = {"sitzung": session.name, "links": str(session.left), "rechts": str(session.right),
            "video": str(session.video) if session.video else None, "zeitachse": timeline_note,
            "versatz_rechts_zu_links_s": round(session.right_offset, 3),
            "sprecher": {"1": names[1], "2": names[2]}, "pegelanalyse": info,
            "beitraege": [{**t, "sprecher": names[t["sprecher"]]} for t in turns],
            "woerter": [{**w, "sprecher": names[w["sprecher"]]} for w in words]}
    base.with_suffix(".json").write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return base


# =========================================================================== main

def process(session, model, args, names):
    print(f"\n=== {session.name}")
    left = load(session.left, session.left_ch)
    right = load(session.right, session.right_ch)
    dur_l, dur_r = len(left) / SR, len(right) / SR

    # Anzeige-Zeitachse: Videostart (falls Stereo-WAV vorhanden), sonst Beginn der fruehesten Aufnahme
    if session.video and not session.left == session.right:
        start_l = session.video_offset
        start_r = session.video_offset + session.right_offset
        timeline = (f"0 = Beginn von {session.video.name} (Video); Links ab {start_l:+.3f} s, "
                    f"Rechts ab {start_r:+.3f} s")
    elif session.left == session.right:
        start_l = start_r = 0.0
        timeline = f"0 = Beginn von {session.left.name}"
    else:
        start_l, start_r = 0.0, session.right_offset
        first = min(start_l, start_r)
        start_l, start_r = start_l - first, start_r - first
        first_file = session.left if start_l == 0 else session.right
        clock = dji_time(first_file)
        timeline = (f"0 = Beginn von {first_file.parent.name}/{first_file.name}"
                    + (f" (Sender-Uhr {clock:%d.%m.%Y %H:%M:%S})" if clock else "")
                    + f"; Links ab {start_l:+.3f} s, Rechts ab {start_r:+.3f} s")
    if min(start_l, start_r) < 0:
        timeline += "; negative Zeiten liegen vor dem Videostart"

    # Interne Zeitachse beginnt bei 0 mit der fruehesten Aufnahme
    shift = max(0.0, -min(start_l, start_r))
    span_l = (start_l + shift, start_l + shift + dur_l)
    span_r = (start_r + shift, start_r + shift + dur_r)
    length = int(round((max(span_l[1], span_r[1]) + 0.5) * SR))
    L = place(left, span_l[0], length)
    R = place(right, span_r[0], length)
    print(f"  Links:  {session.left.parent.name}/{session.left.name}   "
          f"Rechts: {session.right.parent.name}/{session.right.name}")
    print(f"  Zeitachse: {timeline}")

    lev = analyze_levels(L, R, span_l, span_r, args.kalibrierung)
    for line in describe_levels(lev.info, names):
        print(f"  {line}")
    if lev.info.get("warnung"):
        print(f"  WARNUNG: {lev.info['warnung']}")
    if lev.active.any():
        share = np.mean(lev.rel[lev.active] > 0) * 100
        print(f"  Redeanteil nach Pegel: {names[1]} {share:.0f} %, {names[2]} {100 - share:.0f} %")
    if model is None:
        return

    words_l = transcribe(model, L, args.sprache, "Links", span_l)
    words_r = transcribe(model, R, args.sprache, "Rechts", span_r)
    words = smooth_speakers(assign_words(words_l, words_r, lev, args.toleranz))
    for w in words:
        w["start"] = round(w["start"] - shift, 3)
        w["end"] = round(w["end"] - shift, 3)
    turns = build_turns(words)
    base = write_outputs(session, turns, words, lev.info, names, args.ausgabe, timeline)
    print(f"  {len(words_l)} + {len(words_r)} Woerter erkannt, {len(words)} zugeordnet, "
          f"{len(turns)} Redebeitraege")
    print(f"  -> {base}.txt / .srt / .json")


def main():
    global FFMPEG, FFPROBE
    parser = argparse.ArgumentParser(description="Transkript mit Sprechertrennung ueber zwei Mikrofone")
    parser.add_argument("--basis", type=Path, default=SCRIPT_DIR,
                        help="Ordner mit Links/, Rechts/ und optional Stereo/")
    parser.add_argument("--ausgabe", type=Path, default=SCRIPT_DIR / "Transkripte")
    parser.add_argument("--sprecher1", default="Sprecher 1", help="Name fuer das Mikrofon Links")
    parser.add_argument("--sprecher2", default="Sprecher 2", help="Name fuer das Mikrofon Rechts")
    parser.add_argument("--sprache", default="de", help="Sprache fuer Whisper (Standard: de)")
    parser.add_argument("--modell", default="large-v3-turbo",
                        help="Whisper-Modell: tiny, base, small, medium, large-v3, large-v3-turbo "
                             "oder ein Hugging-Face-Repo (z. B. mlx-community/whisper-large-v3-turbo)")
    parser.add_argument("--engine", choices=("auto", "mlx", "faster-whisper"), default="auto",
                        help="auto: MLX auf der GPU bei Apple Silicon, sonst faster-whisper auf der CPU")
    parser.add_argument("--toleranz", type=float, default=2.0,
                        help="dB, die ein Wort auf 'seinem' Kanal leiser sein darf (Standard 2)")
    parser.add_argument("--kalibrierung", type=float, default=None,
                        help="Pegel-Mitte Links-Rechts in dB manuell setzen statt automatisch")
    parser.add_argument("--max-abstand", type=float, default=15,
                        help="max. Abstand der Dateinamen-Zeitstempel fuer Paare in Minuten")
    parser.add_argument("--uhr-toleranz", type=float, default=10,
                        help="Sekunden, die die Uhren der beiden Sender voneinander abweichen duerfen")
    parser.add_argument("--rueckfall-fenster", type=float, default=2.0,
                        help="Suchfenster in s um den Versatz laut Dateinamen fuer die Rueckfallloesung")
    parser.add_argument("--erzwinge-rueckfall", action="store_true",
                        help="Stereo-WAV und direkte Korrelation ignorieren (zum Testen der Rueckfallloesung)")
    parser.add_argument("--nur", help="nur die Sitzung mit diesem Namen bzw. dieser Links-/Rechts-Datei")
    parser.add_argument("--nur-zuordnung", action="store_true", help="ohne Spracherkennung")
    args = parser.parse_args()

    FFMPEG, FFPROBE = find_tool("ffmpeg"), find_tool("ffprobe")
    names = {1: args.sprecher1, 2: args.sprecher2}

    print("Suche zusammengehoerige Aufnahmen ...")
    sessions = find_sessions(args.basis.resolve(), args.max_abstand, args.uhr_toleranz,
                             args.rueckfall_fenster, args.erzwinge_rueckfall)
    if args.nur:
        sessions = [s for s in sessions if args.nur in (s.name, s.left.stem, s.right.stem)]
    if not sessions:
        print("Keine Paare aus Links/Rechts und keine Stereo-WAV gefunden.")
        return 1
    print(f"{len(sessions)} Sitzung(en) gefunden.")

    model = None
    if not args.nur_zuordnung:
        model = WhisperEngine(args.engine, args.modell)
        print(f"Whisper: {model.describe()} (beim ersten Mal wird das Modell heruntergeladen)")

    for s in sessions:
        process(s, model, args, names)
    return 0


if __name__ == "__main__":
    sys.exit(main())
