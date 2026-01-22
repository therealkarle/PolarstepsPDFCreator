# Feature Goal List ✅

Diese Liste dient als ausführliche Checkliste für noch zu implementierende Features und als **Orientierung für die AI** (Priorität, Akzeptanzkriterien, Beispiele). Nutze die Checkboxes, um Aufgaben abzuhaken. Nur MEnschen dürfen Aufgaben abhacken

---

## 🔧 Karten / Map Improvements
- [ ] **Höhere Auflösung** (Option in `config.json`, default höher auf 2× tiles)
  - Akzeptanzkriterien: Karte wird schärfer auf PDF-Titelseite und Schritt-Karten, kein sichtbares Kachel-Flimmern.
- [ ] **Bessere Fit-Algorithmen für Bounds/Outlines** (sicherstellen, dass Routen und Umrisse gut passen)
  - Akzeptanzkriterien: Route nicht abgeschnitten, Padding-Option konfigurierbar.
- [ ] **Doppelte Bilder erkennen & entfernen** (z.B. Fall: "Hochlitten Weihnachtsferien" — Bild wurde mehrfach für verschiedene Schritte verwendet)
  - Akzeptanzkriterien: Algorithmus findet identische/nahe Duplikate nach Pfad/Hash und verhindert wiederholte Verwendung als Marker.
- [ ] **Kombinierbare Kartenlayer** (Satellite + Street + City Name Overlay) auswählbar in `config`
  - Akzeptanzkriterien: Auswahlmöglichkeit in Config; MapGenerator lädt passende Tile-Templates und rendert Overlay-Labels.
- [ ] **Marker-Thumbnail-Caching & Hervorhebung** (aktuelle Step farblich hervorheben)
  - Akzeptanzkriterien: Marker Thumbnails werden in `.map_marker_cache` zwischengespeichert; aktueller Step hat auffälligen Ring.

---

## 😀 Emoji Support
- [ ] **Inline-Emoji-Bilder in Texten** (Twemoji-Fallback, Cache `.emoji_cache`) 💡
  - Akzeptanzkriterien: Emojis in Step-Titel/-Beschreibung werden als kleine Bilder eingebettet; Text bleibt kopierbar; fallback auf Text bei Fehler.
- [ ] **Schreibe Tests / Beispiele** mit Emoji-komplexen Sequenzen (ZWJ, Skin-Tone)

---

## 🖥 UI / UX
- [ ] **Einfach bedienbare GUI** ("mama-friendly")
  - Akzeptanzkriterien: Grundlegende Aktionen: Trip-Auswahl, Rendern, Cache löschen, Config bearbeiten.
- [x] **Mehrere Trips gleichzeitig rendern**
- [ ] **Optionen zur Foto-Auswahl (inkl./exkl.)**
- [ ] **Verschiedene Laguage support**

---

## 🔗 Links & PDF-Details
- [ ] **PDF-Links sichtbar in Blau** (Hyperlinks, z.B. Video-Links, sollten blau und klickbar sein) 🔵
  - Akzeptanzkriterien: ReportLab-Links blau, unterstrichen optional, funktionieren beim Anklicken in PDF-Viewer.
- [ ] **Alle Step-Bilder, die noch nicht gelistet wurden, am Ende sammeln**
  - Akzeptanzkriterien: Am Kapitel-Ende eine Galerie mit allen ungenutzten Bildern in der Reihenfolge der Schritte.
- [ ] **Bessere Seitenumbrüche (weniger verschwendeter Platz)**
  - Akzeptanzkriterien: Heuristik, die Text/Foto-Flowables zusammenhält; weniger leere Bereiche am Ende von Seiten.
- [ ] **Bessere Seitenumbrüche, die weniger "verschwenderisch" sind** (optimieren von KeepTogether/Flowable-Größen)

---

## 📋 QA / Tests / Reproduktionshinweise
- [ ] **Reproduktionsbeispiel für Duplikate**: Testcase mit Trip-Ordner, der gleiche Foto-Datei mehrfach in Steps referenziert (z.B. "Hochlitten Weihnachtsferien").
- [ ] **Unit Tests** für MapGenerator-Zoom, Duplikat-Erkennung, Emoji-Embedding und Photo Grid.

---

## 🧭 AI-Orientierung (wie die AI Aufgaben umsetzt)
Für jede Aufgabe bitte folgende Struktur verwenden:
1. **Ziel** (kurz)
2. **Priorität** (Hoch / Mittel / Niedrig)
3. **Akzeptanzkriterien** (klar & testbar)
4. **Betroffene Dateien/Module** (z.B. `polarsteps_pdf_generator.py::MapGenerator`, `PDFBuilder`)
5. **Test / Reproduktionsanleitung** (inkl. Beispiel-Trip oder JSON-Snippet)

Beispiel:
- Ziel: "Doppelte Bilder entfernen"
- Priorität: Hoch
- Akzeptanzkriterien: "Wenn zwei Marker das gleiche Bild verwenden (SHA1 identisch), wird das Bild nur einmal als Marker verwendet; ungenutzte Mehrfacheinträge werden am Ende der PDF gelistet." 
- Dateien: `polarsteps_pdf_generator.py` (TripParser, MapGenerator)
- Test: Trip-Ordner `BSPData/.../hochlitten-weihnachten_*` mit identischem Bild in Schritt 3 und Schritt 5.

---

