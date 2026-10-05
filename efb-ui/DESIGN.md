# Crew Pad Design Spec ("Qatar-Optik" / flySmart Crew Pad)

Referenz-Mockup: `image_baa77c.png` (Screenshot vom 20.09.2026, 23:17 — liegt in
`C:\Users\Dennis Korolevych\AppData\Roaming\Hermes\composer-images\image_baa77c.png`).
Dies ist die Ziel-Optik der App. Jede UI-Arbeit (Restyling, neue Panels) MUSS
dieser Spec folgen und gegen das Mockup verifiziert werden.

## Layout (Top-Level, NICHT Sidebar-Tiles)

Die App ist ein volles Cockpit-Dashboard (Landscape, Tablet-First), NICHT ein
Tile-Launcher mit Sidebar. Struktur oben nach unten:

1. **Top Bar** (höhe ~48px, dunkel-navy, unterer Rand 1px Amber):
   - Links: Uhrzeit (groß, `23:17`) + Datum (`Sonntag, 20. September`)
   - Logo: `flySmart!` (Ausrufezeichen in Amber)
   - Mitte: Pill-Tabbar: **Übersicht** (aktiv), Zeiten, Flightplan, Route, Bahnen,
     Wetter, Briefing, EDTO — aktive Tab hat helleren navy-Pill-Hintergrund
   - Rechts: `Oceanic 21:17z` + Icons (Sonne/Theme, Sync, Upload/Arrow-up)
2. **Route Hero** (Panel, ~160px):
   - Links ICAO Abflug groß (48px, weiß, Mono), rechts ICAO Ankunft groß
   - Mitte: Fluggesellschafts-Flugnummer groß (`DLH459`), Callsign klein daneben
   - Darunter: **Amber Route-Progressbar** mit Flugzeug-Icon am Ende;
     darunter zentriert: `5 206 NM · 10:22 · FL370`
   - Unterzeilen links/rechts: Abflugzeit + RWY links (`04:55z · RWY 28R`),
     ETA/PLN/RWY rechts — Abweichungen rot (`ON-BLK`, `+75 min`)
3. **Info-Grid** (9 Spalten, Uppercase-Labels, Mono, Werte groß):
   MUSTER (A388 / A380-800), KENNZEICHEN, PAX, ABFLUG-GATE, ANKUNFTS-GATE,
   COST INDEX, REISEVERFAHREN (`CI 20 · M.830`), SELCAL, ETOPS
4. **Status-Band** (Panel mit rotem linken Rand bei DELAY):
   VATSIM·VDGS Status (`DELAY` rot, `+75 min gegen Plan`), EOBT, AOBT, ATOT,
   ETA/IN/PLN (rechts), AIRB·TAXI
5. **Fuel Cards** (4 Karten nebeneinander, große Mono-Zahlen):
   IM FLUG (`100 %`), KRAFTSTOFF START (`136 371 kg`, Block darunter),
   FUEL-CHECK (`—`, noch kein Eintrag), LANDUNG GEPLANT (`18 920 kg`, Reserve)
6. **Info-Rows** (zeilenweise, Icon + Uppercase-Label + Text):
   VATSIM (online-Status, Höhe/GS/Squawk, Warnung rot: `Transponder steht auf
   2000; zugewiesen ist 1723.`), VAMSYS (`Zu diesem OFP ist keine Buchung offen.`),
   WETTER (`KSFo 250/05 IFR · EDDM 270/06 VFR`)

## Farben

| Rolle | Hex |
|---|---|
| App-Hintergrund | `#0d1830` |
| Panel-Hintergrund | `#14213f` |
| Panel-Hintergrund (hell, Hero/Tab-Pill aktiv) | `#1b2c55` |
| Panel-Rand | `#24365f` |
| Text primär | `#f2f5fa` |
| Text sekundär (Labels, Uppercase, Letter-Spacing 0.08em) | `#8fa0c4` |
| Accent (Progressbar, Logo `!`, aktive Highlights) | `#f5a623` |
| Warnung/Delay | `#e0524f` |
| OK/Bestätigt | `#3ecf8e` |

## Typografie

- Zahlen/Werte/ICAO/Flugnummern: **Mono** (JetBrains Mono / Roboto Mono),
  Tabellen-Zahlen mit digit-spacing
- Labels: Uppercase, 11px, Letter-Spacing 0.08em, Text-sekundär-Farbe
- Keine Serifen; alles an ein technisches Cockpit-Instrument-Feeling ausgerichtet

## Verhalten

- Zahlen aktualisieren live (SimConnect/VATSIM/Flight-Sim-Brücke), Status
  DELAY/ON-BLK rot
- Tab "Übersicht" = dieses Dashboard; "Zeiten", "Flightplan", "Wetter" etc.
  = eigene Panels in der gleichen Optik
- TechLog/TECH-Inhalte (Aircraft Health, Defects, Dispatchability) werden in
  diese Optik integriert — nicht als separater Stil

## Verifikation

Jedes Optik-Milestone wird gegen das Mockup-Image verifiziert (Screenshot der
laufenden App vs. `image_baa77c.png`): Layout-Reihenfolge, Farben,
Tab-Labels, Panel-Struktur müssen übereinstimmen.
