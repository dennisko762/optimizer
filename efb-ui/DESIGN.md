# Crew Pad Design Spec — Qatar Airways "QR SmartOps"

**ZIEL-OPTIK (authoritativ, 2.–3.10.2026):** die 5 maroon/Burgundy Qatar-Mockups.
Das alte navy "flySmart!" Dashboard (20.09.) wurde von diesen ersetzt und ist
nicht mehr Ziel.

## Referenz-Bilder (Pflicht, gegen diese verifizieren)

| Datei | Screen |
|---|---|
| `design/qatar-01-lockscreen.png` | iPad-Lockscreen mit Qatar-Wallpaper (Ozean/sonnig + Maroon-Federn), Notifications: MAIL (QR815 DOH→LHR), MESSAGES (QR Ops Gate), WEATHER (MET DOH/LHR), QR OPS (Dispatch ATC slot), SETTINGS |
| `design/qatar-02-crewdesk-inbox.png` | **Crew Desk**: Sidebar (My Flights, Crew Desk aktiv, Profile, QATAR AIRWAYS Logo unten) + Tabs (Inbox aktiv, Trash, Preflight, Weather) + INBOX–21 MESSAGES Liste mit Badges (DISPATCH, A-CDM, NOTAM, WEATHER, A-CDM, VATSIM), rechts Detail-Panel "No message selected." |
| `design/qatar-03-flightplan.png` | **Flightplan** (QR SmartOps): Top-Header (QR815, DOH→LHR, Qatar Airways · B777-300ER, Fri 2 Oct, STD 08:10Z STA 13:20Z EET 7:10), OFP-Info-Banner + "Import New Plan" (gold), Fuel-Zeile (DEVIATION, BLOCK FUEL 213.7t, TAKEOFF FUEL 212.6t, TRIP FUEL 189.4t, PLANNED LDG FUEL 23.3t, EXTRA FUEL, RES + ALTN 10.3t, t/kg-Toggle), große Waypoint-Tabelle (WPT, AWY, FIR, LEG NM, REM NM, ETE, LEG ETE, ALT, WIND, BURN, PLN FUEL, ATO, ACT) |
| `design/qatar-04-route-map.png` | **Route** (QATAR AIRWAYS Logo): Seitenleiste (Map, EDTO, Risks, NAT, Weather, Profile, Altitude) + Karte (Maroon-Töne, offene Küstenlinien) mit Route DOH→SYD, Waypoint-Marker (ABTUN FL360, LERTO FL380, TANPE FL400, IGOGU FL400, PKD FL400, BUNTA FL380, TOVIK FL360), links Layer-Buttons, rechts WX TIME Panel (PLAY, -12h/+12h Slider, NOW), ATC SECTORS VATSIM FL390 (DISPLAY ALTITUDE AUTO/OFF FL320), unten PRECIP + SIGMET Legende (Thunderstorm, Turbulence, Icing, Volcanic Ash, TS Tropical) |
| `design/skynexus-ewas-reference.png` | **SkyNexus eWAS Forecast (IA-Referenz, M5-P)**: Funktions-/Informationsarchitektur der interaktiven Enroute-Wetterkarte — keine Branding-Referenz (siehe unten "M5-P Route Map / eWAS") |
| `design/qatar-05-edto-risks.png` | **EDTO / Risks** (QATAR AIRWAYS): Top-Header (DOH → LHR, QR815 · B777-300ER, Block 7h 25m, STD 18:40Z STA 23:05Z) + Seitenleiste (Map, EDTO aktiv, Terrain, Risks, NAT, Weather, Profile, Altitude) + Route-Karte (DOH–LHR, 0ER1A MAKIN N571 ELBAM M994 TUSKA DCT LHR, 3 631 NM, 7h 25m, LHR/LGW) + **OPERATIONAL RISK INFORMATION** (OFFICIAL RISK NOTICES · 15:59Z · CURRENT, AIRSPACE PERSIAN GULF & GULF OF OMAN ACTIVE, OPERATOR RISK INFORMATION · 15:59Z · CURRENT: UAE LEVEL 3 CAUTION, Oman LEVEL 3 CAUTION, India LEVEL 3 CAUTION, Indonesia LEVEL 3 CAUTION) + **NAT TRACKS · 15:54Z** (NAT A WESTBOUND 02 OCT 1130–1900Z VENIR 5330/20 51/30 48/40 45/50 RAFTN, NAT B WESTBOUND NEBIN 5230/20 50/40 47/50 BOBTU JAROM, NAT C WESTBOUND TOBOR 5130/20 49/30 46/40 43/50 JEBBY) |

## Farben (Qatar Maroon/Burgundy)

| Rolle | Hex (Schätzung aus Mockups) |
|---|---|
| App-Hintergrund (tief) | `#1a0a12` (nahezu schwarzes Maroon) |
| Panel-Hintergrund | `#2a1019` |
| Panel-Hintergrund (hell/Hero) | `#3a1824` |
| Panel-Rand | `#4a2230` |
| Text primär | `#f5ecec` |
| Text sekundär (Labels) | `#c9a8ae` |
| Accent (Qatar-Maroon, aktive Tabs, Badges) | `#8e2a4a` |
| Accent hell (Qatar-Magenta) | `#c74a6a` |
| Gold/Amber (Import, Warnung) | `#e8a838` |
| OK/Bestätigt (VATSIM, CURRENT) | `#4ade80` |
| Gefahr/Aktiv (ACTIVE, NOTAM) | `#ef4444` |
| Wallpaper-Maroon | `#5c1a2e` |

## Typografie

- Zahlen/Werte/ICAO/Flugnummern/Waypoints: **Mono** (JetBrains Mono / Roboto Mono)
- Labels: Uppercase, 11px, Letter-Spacing 0.08em, Text-sekundär-Farbe
- Flugnummer/ICAO in der Hero: sehr groß (32–48px), weiß
- Alles an ein technisches Cockpit-Instrument-Feeling ausgerichtet

## Layout-Prinzipien (aus den 5 Screens)

1. **Top-Header-Band** (jeder Screen): links Uhrzeit+Datum, Mitte Screen-Titel/Route,
   rechts Status (Signal, Batterie, Theme-Icon, Sync-Icon, Menü-Dots).
2. **Sidebar** (Crew Desk + QR SmartOps-Screens): schmal, links, Icons+Labels,
   aktiver Eintrag mit Maroon-Pill + hellerem Rand.
3. **Tab-/Seitenleiste**: Pill-Tabs (Overview/Times/Flightplan/Route/Charts/
   Weather/Briefing/EDTO) — aktiver Tab = Maroon-Pill mit hellem Hintergrund.
4. **Hero-Panel** (Flug): große ICAOs links/rechts, Mitte Flugnummer+Datum+STD/STA,
   darunter ggf. Progressbar.
5. **Info-Grid / Fuel-Zeile**: Uppercase-Labels + große Mono-Werte in Spalten.
6. **Liste+Detail-Panels**: z. B. Inbox mit Badge + Titel + Sender + Zeit rechts,
   Details in separatem rechten Panel.
7. **Karten-Screens**: Maroon-getönte Weltkarte, weiße Routenlinie, Waypoint-Marker
   mit FL-Label, Overlay-Panels (WX TIME, ATC SECTORS, Layers) als dunkle Pills.
8. **Risk/EDTO**: Sections mit grünem "CURRENT"-Punkt, Country-Karten mit Thumbnail,
   LEVEL 3 – CAUTION, NAT-Tracks als Mono-Blöcke.

## Verhalten

- Qatar-Branding durchgängig (Qatar Airways Logo, Oryx-Feder-Muster im Hintergrund,
  Maroon-Palette) — das ist der sichtbare Unterschied zum alten navy flySmart.
- Zahlen aktualisieren live (SimConnect/VATSIM/SimBrief OFP).
- TechLog/TECH-Inhalte (Aircraft Health, Defects, Dispatchability) werden in
  dieselbe Maroon-Optik integriert — nicht als separater Stil.

## Verifikation

Jedes Optik-Milestone wird gegen die 5 Qatar-Mockups verifiziert (Screenshot der
laufenden App vs. `qatar-0X-*.png`): Layout-Reihenfolge, Farben, Tab-Labels,
Panel-Struktur, Qatar-Branding müssen übereinstimmen.

## M5-P Route Map / eWAS (interaktive Wetterkarte)

**Quelle der IA:** `design/skynexus-ewas-reference.png` (SkyNexus eWAS Forecast —
https://www.skynexus.nl/forecast#how). Von SkyNexus wird **nur die
Funktionalität / Informationsarchitektur** übernommen, NICHT das Branding.
Die Optik bleibt Qatar-Maroon / QR SmartOps (`qatar-04-route-map.png` ist das
Optik-Ziel). Implementiert in `src/crew/qatar/MapWeatherPanel.jsx` +
`CrossSection.jsx` + `wxmap.css` (Route-Tab des SmartOps-Screens).

**Karte (MapLibre GL, Dark-Matter-Basemap + OSM/CARTO-Attribution):**
- Exakte Route aus dem aktiven SimBrief OFP (alle Fixes, Original-Reihenfolge,
  Antimeridian-Splitting); cyan-Halo + goldene Linie, Fix-Marker (weiß) mit
  Declutter-Labels, Endpunkte als goldene Ringe, ausgewählter Fix hervorgehoben.
- Klick/Touch auf jeden Fix → Detail-Popover (Ident, Stage, LAT/LON, FL,
  cumulative NM, Airway/FIR, Wind/OAT/Turb/ICE/JET aus dem Route-Sampler).
- Pan/Zoom/Fit-Route per Touch/Maus + Nav-Buttons (＋, －, fit, layers).
- Status-Banner oben links: `cycle / valid at / fetched at / stale` +
  "NOAA GFS proxy" Kennzeichnung; SSE-Ereignis `cycle` → Auto-Refresh der
  geöffneten Karte bei jedem neuen abgeschlossenen GFS-Zyklus.

**Layer-Drawer (rechts, "LAYERS & OVERLAYS"):** FL-Selector (FL050–FL450
Preset-Buttons), unabhängige Toggles für Turbulence / Icing / CAPE / Fronts /
Jet Stream (NOAA-GFS-Proxies, nie offizielle WAFS/eWAS — im Drawer offen
gekennzeichnet), Legende pro Layer (Band-Farben), "unavailable"-Markierung
wenn ein Layer für FL/Zyklus fehlt.

**Zeit-Slider (unten):** T+0–T+36 stündlich, Play/Pause/NOW, DEP→ARR-Band
(Flight-Zeitraum aus ETE). Scrubbing aktualisiert Karte + Profil synchron.

**Docked Cross-Section (unten, Distance × FL):** geplantes
Steig-/Kreuz-/Sinkprofil aus den OFP-Waypoints, jede Fix-Spalte hover/tap
→ hebt denselben Punkt auf der Karte hervor (bidirektionale Verknüpfung).

**Wetter-Engine (Backend `crew_platform/weather/`):** NOAA GFS 0.25°
Zyklen 00/06/12/18 UTC, T+0..T+36; Ellrod/TITAN TI1 (WMO-Skala 5/15/25),
CIP-Icing-Proxy, Jet-Ausdehnung aus |u,v| (60/100 kt), Hewson
θe-Gradient (Bolton-1980) — alle als Proxies deklariert. Polygonisierte
GeoJSON-Bänder; Route-Sampler bilinear an jedem Fix × FL × ETA;
unverfügbare Felder werden als "—"/unavailable angezeigt, nie erfunden.

## Legacy (Nicht-Ziel)

- `design/ref-crewpad-dashboard.png` + `design/ref-ipad-home.png` +
  `design/legacy-navy-flysmart-map.png` = altes navy "flySmart!" (20.09.) —
  nur noch Referenz für die Tile-Shell-Funktionalität (eDesk-Checkin, OFP,
  Notifications, Boarding), NICHT für die Optik.
