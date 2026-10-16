# Learnings

Erkenntnisse aus der Entwicklung (Stand 2026-10-06). Hier steht, was sich **nicht** aus dem
Code ablesen lässt: Messwerte, verworfene Wege, Fallstricke und offene Fragen.

## Aufnahme-Setup und Eigenheiten der Hardware

- **DJI Mic 2, zwei Sender:** Laufwerksname „Links“ = Sprecher 1 (Robert), „Rechts“ = Sprecher 2.
  Die Sender nehmen intern in **32-Bit Float, 48 kHz, Mono** auf. Dateien haben weder Timecode
  noch eingebettete Metadaten (Chunks nur `fmt` + `data`). Die Startzeit steckt nur im
  Dateinamen `DJI_NN_YYYYMMDD_HHMMSS`. Das Ende der Aufnahme ist im Timestamp der Datei selbst.
- **Die Uhr der Sender geht ca. 29–30 s nach** gegenüber der iPhone-Kamera. Die beiden Sender
  untereinander weichen nur um ≤ 0,6 s ab. → Zeitstempel aus den Dateinamen taugen als grober
  Startwert, nicht zum Synchronisieren. Vor Aufnahmen die Senderuhr über die DJI-App stellen.
- **Kamera:** iPhone 13 mit Blackmagic Cam, Ton vom DJI-Empfänger (Stereo: L = TX1, R = TX2).
  In Blackmagic Cam sollte **Lineares PCM** und **48.0 kHz** eingestellt sein. Dies resultiert
  in 16 Bit Integer.
- **Die Stereospur der Kamera trennt die Sprecher schlecht** (Korrelation L/R 0,54–0,65):
  Beide Mikrofone hören im selben Raum beide Sprecher. Für die Transkription deshalb immer die
  DJI-Einzeldateien nehmen, die Stereospur nur als Zeitreferenz. Diese Spur ist nicht notwenig
  und aufwendig zu erzeugen.

## Pegel und 32-Bit Float

- Sprache liegt bei ca. **−30 bis −32 LUFS**, Spitzen (Klatschen) bis **+7,7 dBFS**. Das ist in
  Float verlustfrei. Normale Player (QuickTime) spielen das aber sehr leise ab bzw. schneiden
  über 0 dBFS ab.
- **Entscheidung: am Sender nicht mehr Verstärkung einstellen.**
  - Die Spitzen liegen schon über 0 dBFS.
  - Die Kameraspur würde übersteuern, und die ist in AAC/16 Bit nicht zu retten.
  - Für die Transkription bringt es nichts (Whisper bekommt einen normalisierten Pegel, die
    Sprecherzuordnung nutzt nur Pegel*differenzen*).
  - Unklar, ob das Mic 2 die Verstärkung im 32-Bit-Float-Modus überhaupt anwendet (im Handbuch
    der Firmware prüfen).
  - Stattdessen: separate Hörkopien (`anhoeren.py`).
- **Crest-Faktor der Ansteckmikros ist hoch:** Kurze Spitzen (Plosive, Lachen) liegen 17–21 dB
  über der Lautheit. Die Kurzzeit-Lautheit der lauten Passagen ist nur ca. 3 dB über dem Mittel,
  der Unterschied zwischen eigener Stimme und Übersprechen ist also *nicht* das Problem.
- **Hörkopien nie für die Transkription verwenden:** Der Kompressor verfälscht die Pegeldifferenz
  zwischen den Kanälen.

### Verworfen bei `anhoeren.py`

1. **`loudnorm` (zwei Durchgänge):** schaltet wegen der Spitzen in den dynamischen Modus und
   landete bei −17 bis −21 LUFS statt −16.
2. **Feste Verstärkung + `alimiter`:** Der Limiter frisst 2–3 LU (selbst mit Grenze 0 dBFS).
   Ergebnis −18 bis −20 LUFS.
3. **Kompressor *nach* der Verstärkung mit fester Schwelle:** konvergiert nicht. Mehr Verstärkung
   → mehr Kompression → die Korrektur jagt sich selbst (+87 dB und trotzdem −31 LUFS).
4. **Funktioniert:** Kompressor *vor* der Verstärkung, Schwelle relativ zur gemessenen Lautheit
   (L_in + 10 dB). Danach Verstärkung, dann der Limiter mit 4-facher Abtastrate (sonst
   Spitzen zwischen den Samples bis −0,1 dBTP), nachmessen und korrigieren.

## Paarbildung und Synchronisation (Links ↔ Rechts ↔ Stereo)

Gemessene Versätze (Referenz für Tests):

| Paar | Versatz Rechts zu Links | Lage im Video |
|---|---|---|
| Links/DJI_03 + Rechts/DJI_04 | +14,440 s | C007: Links ab +52,086 s, Rechts ab +66,525 s |
| Links/DJI_04 + Rechts/DJI_05 | −2,632 s | C008: Links ab −9,968 s, Rechts ab −12,600 s |
| Links/DJI_02 + Rechts/DJI_02 | −121,186 s | kein Video |

- **Stereo-WAV ↔ DJI-Datei:** Die Kreuzkorrelation der Wellenform ist sehr zuverlässig
  (Peak-Verhältnis 6–19), weil der Empfänger dasselbe Signal aufzeichnet.
- **Links ↔ Rechts direkt** (zwei verschiedene Mikrofone): Eine globale Kreuzkorrelation klappt
  bei langen Aufnahmen. Bei kurzen Clips (C008, 23–27 s) liefert sie einen **falschen Versatz**
  (7,05 s statt −2,63 s).
- **Verworfen:**
  - **Globale Korrelation der Pegelverläufe:** schlechter als die Wellenform (Peak-Verhältnis
    1,8–4).
  - **GCC-PHAT global:** richtig bei C007, falsch bei C008.
  - **Nur die Zeitstempel aus den Dateinamen:** Abweichung von Sekunden, zu ungenau.
- **Funktioniert als Rückfallösung:** Versatz grob aus den Dateinamen, dann die Korrelation der
  Pegelverläufe **nur im Fenster ±2 s**. Das trifft alle drei Paare auf ≤ 10 ms (r = 0,49 bis
  0,95). Fehlpaarungen verhindert die Bedingung „Aufnahmen überlappen sich laut Dateinamen
  ≥ 3 s“.
- **Nutzen der Stereo-WAV:** Sie ist nur nötig für (a) sehr kurze Clips ohne Rückfall und
  (b) die Zeitachse des Videos (SRT passend zum Video). Für die Qualität der Transkription
  bringt sie nichts. Sie ist auch keine extra Aufnahme, sondern die Tonspur der MOV
  (`mov_extract.py`).
- **Tipp beim Aufnehmen:** Kurz klatschen am Anfang gibt beiden Mikrofonen ein gemeinsames
  Signal.

## Sprechertrennung über die Pegeldifferenz

- Gemessene Trennung bei Interview-Abstand: **~23 dB** (Sprecher 1 +11,3 dB / Sprecher 2
  −11,3 dB um die Kalibriermitte). Bei der Testaufnahme vom Mittag nur 5,5 dB → unsicher.
- **Kalibrierung nur im gemeinsamen Bereich:** Die erste Version maß auch dort, wo nur ein Sender
  lief (der andere Kanal war Nullen). Das ergab eine absurde „Trennung von 122 dB“.
- **Ansatz:** Jeder Kanal wird *vollständig* transkribiert, danach werden nur die Wörter
  behalten, bei denen der eigene Kanal dominiert. Gleichzeitiges Sprechen bleibt so erhalten.
  Ein Zuschneiden in Segmente vor Whisper wurde nicht umgesetzt (weniger Satzkontext, Gefahr
  bei Überlappung). Das wäre aber der Weg, die Rechenzeit zu halbieren, denn ca. 50 % der
  Whisper-Arbeit entfällt auf Übersprechen, das danach verworfen wird.
- **Wort-Zeitstempel von Whisper sind der kritische Punkt.** Die Zuordnung hängt an ihnen.
  Darum gibt es die Suche ±0,3 s, wenn ein Wort „in der Stille“ liegt.

## Whisper: Engines und Modelle (Test an C007, 23 Prüfstellen)

| Variante | Rechenzeit beide Kanäle (je ~5 min) | Treffer |
|---|---|---|
| MLX large-v3-turbo (GPU) | 23 s | 22/23 |
| MLX large-v3 (GPU) | 47 s | 15/23 |
| faster-whisper large-v3-turbo (CPU, int8) | 156 s | 23/23 |
| faster-whisper large-v3 (CPU) | 639 s | 19/23 |

- **Entscheidung: Standard ist MLX turbo** (M2 Max, ~5 s Rechenzeit pro Minute Aufnahme).
  **Für die höchste Genauigkeit: `--engine faster-whisper`.** Der einzige Unterschied war
  „Vormittag geregnet“ (CPU) vs. „Wohnmeld auch gerechnet“ (MLX): mlx-whisper kann keine
  Beam-Search und wählt immer sofort das wahrscheinlichste Wort.
- **large-v3 (ohne Turbo) verworfen:** Bei langen Dateien liegen die Wort-Zeitstempel 1–3 s
  daneben (z. B. „Was ist denn…“ 76,5 s statt 77,8 s). Satzanfänge fallen dadurch aus der
  Sprecherzuordnung. Bei einem 20-s-Ausschnitt erkennt es identisch. Das Problem ist also die
  Zeitzuordnung, nicht die Erkennung. Dazu ist es 2–4× langsamer. Beide Modelle wurden aus dem
  HF-Cache gelöscht.
- **Deutsch-Feintuning `whisper-large-v3-turbo-german-f16` verworfen:** Zahlen werden
  ausgeschrieben („siebzehn“), „ss“ statt „ß“, eigene Fehler („Ersatzschloss“). Kein Gewinn.
- **`hallucination_silence_threshold` bei mlx-whisper nicht setzen:** Mit 2,0 verschwand der
  echte Satz „Ich habe sie gezählt, zweimal“.
- Das Wort „Ersatzschloss“ schreiben alle Modelle gleich. Vermutlich wurde es so gesagt, also
  kein Erkennungsfehler.

## Fallstricke

- **Python-Version:** `X | None` in Dataclass-Annotationen stürzt unter Python ≤ 3.9 ab
  (macOS-System-Python ist 3.9.6, unter Windows evtl. auch älter). → `Optional[...]` verwenden.
  Alle Scripte müssen mit Python 3.9 kompilieren.
- **IntelliJ/PyCharm:** Das Projekt war als Web-Modul ohne Python-SDK angelegt. Folge:
  Fehlalarme wie „Levels doesn't have attributes power/average“ (numpy wird nicht aufgelöst).
  → `.venv` als SDK zuweisen.
- **macOS legt `._*`-Dateien und Systemordner** (`.Spotlight-V100`, `.Trashes`, `.fseventsd`)
  auf den DJI-Laufwerken an. Beim Import ignorieren. Versehentlich landeten auch IDE-Dateien
  (`.idea`, `Links.iml`) auf dem Sender.
- **macOS-Dateisystem ist case-insensitive:** `clip.MOV` und `clip.wav` vertragen sich, aber
  keine zwei Dateien, die sich nur in Groß-/Kleinschreibung unterscheiden.
- **Der Ordner `Stereo/` wurde zwischenzeitlich entfernt.** Die Scripte müssen ohne ihn
  funktionieren (tun sie: Rückfall).
- **Beim Löschen von Modellen im HF-Cache zeigt `df` keinen Gewinn:** Lokale
  APFS/Time-Machine-Schnappschüsse halten den Platz zunächst fest.

## Offene Punkte

- **Testaufnahmen vom Mittag:** Links/DJI_01 und Rechts/DJI_01 haben keinen Partner. Das Paar
  Links/DJI_02 + Rechts/DJI_02 ist fast nur einkanalig (6,8 s gemeinsam), die Zuordnung
  bleibt unsicher.
- **`Links/DJI_01_20261006_154027.WAV`** (neu, 06.10.) hat noch keinen Partner in `Rechts/`.
- **`Transkripte/`** stammt noch vom CPU-Lauf mit Stereo-Zeitachse. Neu erzeugen mit MLX ist
  offen (ohne `Stereo/` ändern sich die Dateinamen und die Zeitachse).
- **Kurze Fetzen im Vorgespräch** („Das ist“ / „Das“), solange beide an den Mikros hantieren.
  Die Glättung hilft nur teilweise.
- **Optimierung für die Rechenzeit** (nur dominante Abschnitte je Kanal transkribieren):
  vorgeschlagen, nicht umgesetzt, Qualitätsvergleich steht aus.
- **Verstärkung am DJI Mic 2 im 32-Bit-Float-Modus:** Wirkung laut Handbuch noch nicht geklärt.
- **Sprechernamen** sind Platzhalter („Sprecher 1/2“). `--sprecher1 Robert --sprecher2 Matthias`
  wurde noch nicht als Standard festgelegt.
