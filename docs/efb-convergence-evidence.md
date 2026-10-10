# EFB convergence pass — acceptance evidence (card t_a4119b80)

Audit source: `t_af01ae33` ranked gap report against the archived qatar-0x
mockups, the SkyNexus eWAS reference and `efb-ui/DESIGN.md`.
Base commit: `20268a20` (origin/main at the time of the audit).

Environment for the live checks:

- `npm ci && npm run build` in `efb-ui`, served by
  `uvicorn optimizer.api.app:app` on `127.0.0.1:7071` (serves `efb-ui/dist`).
- Chromium via CDP, viewport emulation at 1920x1080, 1440x900, 1366x1024,
  1194x834, 1024x768, 834x1194 and 768x1024.
- TechLog SQLite migrated with `alembic upgrade head`.
- Honest-state caveat: SimConnect, SimBrief, Navigraph and vAMSYS are not
  configured in this environment, so every screen below is the *unavailable*
  variant. That is the behaviour under test — no value was stubbed in.

Before screenshots: the audit set under the QA profile's
`cache/scratch/efb-audit/` (same base commit).
After screenshots: `cache/scratch/efb-after/` (this run).

## Acceptance checks

| # | Check | Result | Evidence |
|---|---|---|---|
| 1 | Tabs unclipped at 7 widths | PASS | At every width: 8/8 SmartOps and 5/5 Crew Desk tabs sit inside the 48 px bar (`y >= 0`, `bottom <= bar.bottom`), one row, 44 px tall. At <=1024 px the strip scrolls horizontally (edge fade + scroll-snap) and the off-screen tabs stay clickable (EDTO verified at 768 px). `flightplan-*`, `crewdesk-*`, `tab-EDTO-768.png` |
| 2 | Eight tabs, eight distinct screens | PASS | Pairwise comparison of `document.body.innerText` across all 8 tabs: 0 identical pairs (was: overview/times/briefing byte-identical to flightplan). `tab-*-1920.png` |
| 3 | Briefing renders BriefingPanel | PASS | Briefing tab shows the ATIS / METAR / TAF / SIGMET / SIGWX sections for both stations. `tab-Briefing-1920.png` |
| 4 | Weather and Route share the map contract | PASS | Both render `MapWeatherPanel` with identical `telemetry`, `simConnected`, `live`, `flight`, `apiBase`; only `variant` differs. Unit-asserted in `smartOpsConvergence.test.jsx`. |
| 5 | Route carries the qatar-04 IA | PASS | Labelled "Layers & Overlays" control, ATC SECTORS panel (VATSIM badge + honest NOT CONFIGURED), ROUTE TERRAIN card, PRECIP + SIGMET legend, WX TIME with PLAY / -12h / +12h / NOW. `tab-Route-1920.png`, `route-layers-drawer-1920.png` |
| 6 | Maroon basemap, not CARTO grey | PASS | Rendered map pixels sampled over land: `#2d111b`, `#2c111a`, `#201317` (token `#2a1019`); water `#1a0a12`. `tintBasemap` unit-tested. `tab-Route-1920.png` |
| 7 | Route fixes == Flightplan rows | PASS | QR815 DOH-LHR, sim-planned: Flightplan table 13 rows, Route map "13 fixes", labelled DERIVED with the provenance line "showing the derived sim-planned great-circle route (not a filed route)". |
| 8 | 44x44 px hit boxes | PASS | Every interactive control on Home, Crew Desk, Tech Log, Profile and the 8 SmartOps tabs measured at 1920 and 1024: 0 under 44 px (was 15/15 Route controls under 44). Documented exceptions: `<input type=range>` sliders, whose 44 px row is provided by their container. |
| 9 | Tech Log replaces the Crew Desk content | PASS | With Tech Log selected: `.qr-inbox` 0, `.qr-detail` 0, full-width `.qr-crewdesk__full` 1, TechPanel honours `embedded`. `techlog-1920.png` |
| 10 | Real inbox timestamps | PASS | All rows render `DD MMM · HH:MMz` (e.g. `10 OCT · 15:12z`); the detail panel repeats the stamp. Rows without a feed timestamp read `NO TIMESTAMP` rather than a fabricated time. `crewdesk-1920.png` |
| 11 | Failed refresh is honest | PASS | Backend stopped, REFRESH pressed: `STALE — Refresh failed (Failed to fetch) — offline? Showing last loaded messages.`, the 6 loaded rows stay, the selected message stays selected. |
| 12 | Qatar selectable + persisted | PASS | `/api/crew/providers` lists `qatarvirtual` first; selecting it writes `qr.crew.provider.v1` and a reload lands straight in the shell. Profile shows AIRLINE = the selected provider plus INTERFACE = "QR SmartOps (Qatar Airways reference shell)". `provider-gate-1920.png`, `profile-1920.png` |
| 13 | EDTO composition | PASS | Full-width route summary bar (ROUTE / ROUTE STRING / DISTANCE / FLIGHT TIME (PLAN) / ALTERNATE(S)) with the risk content full width below; NAT blocks are 4 lines (name+direction / coded track / FL band / decoded lat-long chain); the empty map half is gone. `tab-EDTO-1920.png` |
| 14 | `tests/test_techlog_aircraft_api.py` green | PASS | 8/8 pass (was 3 failing). Root cause fixed, not the assertion: `db.py` and `database.py` each owned an engine, so `reset_engine()` left the other alive and rows leaked between temp databases; `db.py` now delegates to the single owner. `flight_hours` / `flight_cycles` default to 0 in the model, matching the alembic baseline. The three ids were removed from `docs/test-baseline-failures.json`. |
| 15 | QatarShell coverage >= 60 % stmts | PASS | `QatarShell.jsx` 65.40 % stmts / 67.00 % lines (was 27.44 %), with tests asserting rendered screen identity per tab, the Crew Desk states and the tab-bar CSS contract. |

## Gates

| Gate | Result |
|---|---|
| `efb-ui` eslint | PASS (0 problems) |
| `efb-ui` vitest | PASS — 304 tests / 25 files |
| `efb-ui` production build | PASS |
| `efb-ui` diff-cover vs origin/main (>=80 %) | PASS — 83 % |
| backend pytest | 674 passed / 5 skipped; 1 pre-existing baseline failure (`test_docs_exist_and_mention_main_sources`) and the 2 pre-existing uncollectable wind modules |
| `check_test_baseline.py` | PASS — no newly introduced failures |
| backend diff-cover vs origin/main (>=80 %) | PASS — 100 % |
| ruff (`crew_platform`, `tests`) | PASS |

## Deliberately NOT changed

- No honest "unavailable" state was replaced with a value: Navigraph 8x NOT
  CONFIGURED, vAMSYS readiness, SIM DISCONNECTED, SIM PLANNED / no OFP, the
  NOAA GFS proxy disclaimer and the EDTO static snapshot badge all stay.
- `-12h` on the WX TIME stepper walks back toward NOW and disables there:
  the GFS proxy publishes T+0..T+36 only and there is no reanalysis behind
  it, so a negative forecast hour would be fiction.
- ATC SECTORS renders as NOT CONFIGURED (no VATSIM feed is wired) and ROUTE
  TERRAIN states that no terrain dataset is configured, instead of the
  previous hardcoded "FL390" and "Terrain data available".
- Country risk thumbnails read NO MAP instead of being blank maroon boxes.
- Crew login is still the documented mock; no credential handling changed.
- `qatar-01` lockscreen remains out of scope for a web PWA (audit G14).

## Known residual gaps (not in this pass)

- Crew Desk sidebar cabin-window photograph and the Oryx watermark on the
  empty detail panel are licensed imagery and are still absent.
- Runways remains OFP-driven only; Navigraph airport data is a licensed
  datatype reported as NOT CONFIGURED.
- `RouteScreen` (the pre-M5-P SVG route view with the Navigraph chart-tile
  underlay) is still exported and unused; removing it would also remove the
  only code path that draws proxied Navigraph enroute tiles, so it is left
  for a dedicated decision rather than deleted here.
