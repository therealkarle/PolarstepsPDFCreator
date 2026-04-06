# Polarsteps PDF Generator — Deutsch

**Die grafische Oberfläche (Tkinter) ist die empfohlene Benutzeroberfläche; eine voll ausgestattete Kommandozeile steht ebenfalls zur Verfügung.**

Erzeuge ansprechende PDF‑Reisejournale aus deinen heruntergeladenen Polarsteps‑Daten.

## Funktionen

- 🗺️ **Übersichtskarte**: Satellitenkarte mit kompletter Route und Schritt‑Markern
- 📍 **Schritt‑Karten**: Einzelne Karten für jeden Schritt (ESRI World Imagery)
- 📸 **Fotogalerien**: Adaptives Fotolayout (1–6 Fotos pro Schritt)
- 📎 **Zusatzmedien‑Anhang**: Nicht gezeigte Fotos und alle Video‑Links am Ende
- 🌡️ **Wetterinformationen**: Temperatur und Bedingungen für jeden Schritt
- 📝 **Beschreibungen**: Voller Reisetext mit Formatierungen
- 🖥️ **Tkinter‑GUI**: Hauptoberfläche; Trips anzeigen, sortieren und rendern.
  - 📅 **Optionaler Datumswähler** (benötigt `tkcalendar`)
- ⚙️ **Einstellungen**: `config.toml` direkt in der GUI bearbeiten und Pakete verwalten
  - Neuer Button **Install Uninstalled** installiert nur fehlende Pakete
- 💾 **Cache‑System**: Verfolgt gerenderte Trips, erlaubt standardmässiges Überspringen
- 🔄 **Stapelverarbeitung**: Mehrere Trips rendern mit Datums‑Filtern

## Installation

1. Stelle sicher, dass Python 3.8+ installiert ist.
2. Installiere die Abhängigkeiten:

```bash
pip install -r requirements.txt
```

> 💡 **Tipp:** Für die GUI empfiehlt sich die obige Installation. Playwright wird beim
> ersten Rendern automatisch nachgeladen, du kannst es aber auch vorab installieren:

```bash
pip install playwright
playwright install
```

Alternativ kannst du fehlende Pakete direkt in der GUI installieren: Öffne den
**Settings**‑Tab und wechsle zum **Packages**‑Bereich; klicke **Install Uninstalled**,
um nur die nicht vorhandenen Anforderungen (inkl. optionaler Komponenten wie
`tkcalendar`) zu installieren. Die GUI kann dich auch vor dem ersten Rendern
auffordern, Playwright oder andere optionale Bibliotheken zu installieren.

### Schnellstart – GUI (Tkinter)

Die grafische Oberfläche ist für die meisten Nutzer die einfachste Bedienform.

1. Starte die GUI mit:

```powershell
python -m gui.tk_gui
```

oder auf Windows per Doppelklick auf `scripts\run_gui.bat`.

2. Im Programmfenster:
   * Wähle einen oder mehrere **Polarsteps Data**‑Ordner (Pfade mit Semikolon trennen).
   * Optional: Ausgabeordner für PDFs ändern.
   * Wähle Trips in der Liste aus; Spaltenköpfe sind klickbar zum Sortieren (▲/▼ zeigt
     die aktuelle Richtung). Standardmäßig nach Startdatum (neueste zuerst) sortiert.
   * Klicke **Render Selected**, um den Vorgang zu starten. Ein Fortschrittslog wird
     im unteren Bereich angezeigt.

Der **Settings**‑Tab erlaubt das direkte Bearbeiten von `config.toml`, zeigt die
aktuelle Umgebung und verwaltet Pakete. Der Button **Install Uninstalled** fügt
nur fehlende Pakete aus `requirements.txt` (und optionale Extras) hinzu.

> ⚠️ Hinweis zum Packen: Für eine einzelne Windows‑EXE kann `pyinstaller` verwendet
> werden. Denke daran, Playwright‑Browser beim Erstellen eines Distributionspakets
> gemäß der Playwright‑Dokumentation einzubinden.

### Optionale CLI‑Werkzeuge

Wenn du lieber die Kommandozeile nutzt oder automatisierte Jobs betreibst, kann
das Skript direkt ausgeführt werden. GUI und CLI teilen sich die gleiche Logik,
weshalb Funktionen in beiden Modi verfügbar sind.

## Verwendung (CLI)

Programm starten:

```bash
python polarsteps_pdf_generator.py
```

Mit benutzerdefiniertem Polarsteps‑Ordner und Ausgabepfad:

```bash
python polarsteps_pdf_generator.py /path/to/PolarstepsData --output-folder /path/to/output
```

### Verfügbare Befehle (im Prompt)

```
cancel        - Programm beenden
clear-cache   - Gerenderte Trips‑Cache löschen
stop          - Während dem Rendern: 'stop' + Enter zum Abbruch
trips         - Alle Trips anzeigen
help/h/?      - Hilfe anzeigen

render [flags] [selection]   (oder 'r' kurz)
```

Hinweise:
- `render` benötigt entweder eine Auswahl (z. B. `r 1;4` oder `r 1,3`) oder ein
  Mode‑Flag:
  - `-a` rendert alle Trips (auch bereits gerenderte)
  - `-ur` rendert nur ungerenderte Trips
- Wenn du `r` ohne Auswahl/Flag eingibst, fragt das Programm:
  `No selection or mode given. Render ALL trips? (yes/no) or enter a different command:`

### Render‑Flags

| Flag | Beschreibung |
|------|--------------|
| `-a`, `--all` | Alle Trips einschließen (auch bereits gerenderte) |
| `-ur`, `--unrendered` | Nur ungerenderte Trips |
| `-y YEAR` | Nach Jahr filtern (z. B. `-y 2025`) |
| `--combined-html [DATEI]` | Erzeuge eine kombinierte HTML-Übersicht für ausgewählte Reisen oder Filter. Standard: `TripPdfs/combined_trips.html`. |
| `-d START;END` | Datumsbereich im Format `dd.mm.yyyy` |
| `-config(KEY=VALUE,...)` | Temporäre Config‑Überschreibungen (z. B. `-config(map_style="road", max_photos_per_step=4)`) |

### Auswahlformate

| Format | Bedeutung |
|--------|-----------|
| `1` | Einzelner Trip |
| `1;4` | Bereich (Trip 1 bis 4) |
| `1,5,6` | Mehrere spezifische Trips |
| `l` oder `last` | Letzter Trip |
| `l-1` | Vorletzter Trip |

### Statistik 📊

Mit `stats` oder `s` wird eine Zusammenfassung der Trips angezeigt, alternativ:

```bash
python polarsteps_pdf_generator.py --stats
```

Filter wie `-y YEAR` oder `-d START;END` funktionieren auch für die Statistik.

### Beispiele

```
r -a                      Render alle Trips (inkl. bereits gerenderter)
r -ur -y 2025             Render ungerenderte Trips aus 2025
r -d 01.01.2025;01.06.2025 -ur   Render Trips im Datumsbereich (nur ungerenderte)
r 1;4                     Render Trips 1 bis 4
r -a l                    Render letzten Trip (auch wenn gerendert)
r 1,3,5                   Render Trips 1, 3 und 5
r 67 -config(map_style="road", max_photos_per_step=4)  Render Trip 67 mit Overrides
```

## CLI‑Optionen

```bash
# Cache löschen
python polarsteps_pdf_generator.py --clear-cache

# Hilfe
python polarsteps_pdf_generator.py -h

# Update‑Prüfungen
python polarsteps_pdf_generator.py --check-update    # prüft, ob es eine neue Version gibt
python polarsteps_pdf_generator.py --update          # lädt neue Version herunter und beendet
python polarsteps_pdf_generator.py --auto-update     # einmalige Update‑Prüfung (überschreibt config)
```

Der `config.toml`‑Schlüssel `auto_update` (boolean, Standard `false`) steuert,
wenn das Programm beim Start automatisch nach neuen Releases sucht.

## Erwartete Datenstruktur

Das Skript erwartet die Polarsteps‑Daten im folgenden Aufbau:

```
BSPData/
  └── {date}/
      └── trip/
          └── {trip-slug}_{trip-id}/
              ├── trip.json
              ├── locations.json
              └── {step-slug}_{step-id}/
                  ├── photos/
                  │   └── *.jpg
                  └── videos/
                      └── *.mp4
```

## Konfiguration

Bearbeite `config.toml`, um die PDF‑Generierung anzupassen. TOML ist lesbar und
unterstützt Kommentare. Das Skript verwendet `config.toml` und fällt auf eine
alte `config.json` zurück, falls diese vorhanden ist.

Beispiel‑Ausschnitt (`config.toml`):

```toml
# Input/output locations
polarsteps_data_folder = "C:/path/to/your/PolarstepsData"
output_folder = "C:/path/where/pdfs/are/saved"

# Fonts
step_title_font_size = 18
step_text_font_size = 12
text_font_path = "C:/Windows/Fonts/SegoeUI.ttf"
emoji_font_path = "C:/Windows/Fonts/seguiemj.ttf"
emoji_scale = 1.2

# Layout
safety_margin_mm = 12
max_photos_per_step = 6
appendix_show_undisplayed_media = true

# Map settings
map_style = "hybrid"
marker_thumb_size = 40
```

Wichtige Optionen (kurz):

- `map_style`: `hybrid` (Satellit) oder `road` (Straßenkarten)
- `step_title_font_size`, `step_text_font_size`: Schriftgrößen
- `marker_thumb_size`: Basisgröße der Kartenmarkierungen (px)
- `max_photos_per_step`: Max. Fotos pro Schrittseite
- `appendix_show_undisplayed_media`: Zeige nicht verwendete Medien im Anhang
- `language`: UI/PDF Sprache (z. B. `en` oder `de`)

## Cache‑System

Das Programm verwaltet `rendered_trips_cache.json`, um nachzuverfolgen, welche
Trips bereits gerendert wurden. Funktionen:

- Bereits gerenderte Trips mit ✓ markieren
- Standardmäßig überspringen (nutze `-a`, um sie einzuschließen)
- Cache mit `clear-cache` löschen

## Ausgabe

Die PDFs werden im Ordner `TripPdfs/` neben dem Skript gespeichert.

## Lizenz

MIT License

---

> ⚠️ **Hinweis:** Früher existierte eine separate `README_GUI.md` mit GUI‑Hinweisen.
> Alle relevanten Informationen wurden in diese Datei übernommen. Die alte Datei
> verweist jetzt auf dieses Dokument.
