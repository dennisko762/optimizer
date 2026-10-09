from __future__ import annotations

from dataclasses import dataclass
from math import ceil


@dataclass(frozen=True)
class AircraftCatalogEntry:
    simbrief_code: str
    config_key: str
    aircraft_type: str
    family: str

    seats: int
    mtow_kg: int | None = None
    mlw_kg: int | None = None

    aircraft_category: str = "jet"
    passenger: bool = True

    # Cost/performance defaults
    reference_fuel_flow_kgph: int | None = None
    ci_min: int = 0
    ci_max: int = 100
    ci_step: int = 5

    notes: str | None = None


# ---------------------------------------------------------------------
# Cost model baseline
# ---------------------------------------------------------------------
#
# Your current A320 YAML is treated as the baseline:
#
# A320:
#   seats: 168
#   maint: 1100 EUR/h
#   own:   1400 EUR/h
#   sched: 400 EUR/h
#   ff:    4000 kg/h
#
# Scaling is intentionally conservative and transparent.
# It is not airline-contract-accurate.
#
# Later:
# - VA/operator-provided costs
# - airline-specific aircraft config
# - OpenAP/BADA-derived performance
# - SimBrief OFP-derived fuel/time validation
#

BASELINE_A320_SEATS = 168
BASELINE_A320_MTOW_KG = 78000
BASELINE_A320_MAINT_EUR_H = 1100
BASELINE_A320_OWN_EUR_H = 1400
BASELINE_A320_SCHED_EUR_H = 400
BASELINE_A320_FF_KGPH = 4000


# ---------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------
#
# This is the curated layer.
# It maps common SimBrief/ICAO aircraft types to local optimizer configs.
#
# The generator can still create YAMLs for unknown SimBrief types, but those
# will be marked as estimated/unsupported until you add a catalog entry.
#

AIRCRAFT_CATALOG: dict[str, AircraftCatalogEntry] = {
    # Airbus A320 family
    "A318": AircraftCatalogEntry("A318", "a318", "A318", "A320", seats=107, mtow_kg=68000, mlw_kg=57000, reference_fuel_flow_kgph=3200),
    "A319": AircraftCatalogEntry("A319", "a319", "A319", "A320", seats=138, mtow_kg=75500, mlw_kg=62500, reference_fuel_flow_kgph=3500),
    "A320": AircraftCatalogEntry("A320", "a320", "A320-200", "A320", seats=168, mtow_kg=78000, mlw_kg=66000, reference_fuel_flow_kgph=4000),
    "A321": AircraftCatalogEntry("A321", "a321", "A321-200", "A320", seats=200, mtow_kg=93500, mlw_kg=77800, reference_fuel_flow_kgph=4700),
    "A19N": AircraftCatalogEntry("A19N", "a319neo", "A319neo", "A320", seats=140, mtow_kg=75500, mlw_kg=62500, reference_fuel_flow_kgph=3300),
    "A20N": AircraftCatalogEntry("A20N", "a320neo", "A320neo", "A320", seats=180, mtow_kg=79000, mlw_kg=67400, reference_fuel_flow_kgph=3600),
    "A21N": AircraftCatalogEntry("A21N", "a321neo", "A321neo", "A320", seats=215, mtow_kg=97000, mlw_kg=79200, reference_fuel_flow_kgph=4300),

    # Airbus A300/A310
    "A306": AircraftCatalogEntry("A306", "a306", "A300-600", "A300", seats=266, mtow_kg=171700, mlw_kg=140000, reference_fuel_flow_kgph=7600),
    "A310": AircraftCatalogEntry("A310", "a310", "A310-300", "A310", seats=220, mtow_kg=164000, mlw_kg=123000, reference_fuel_flow_kgph=7000),

    # Airbus A330 family
    "A332": AircraftCatalogEntry("A332", "a332", "A330-200", "A330", seats=247, mtow_kg=242000, mlw_kg=182000, reference_fuel_flow_kgph=8200),
    "A333": AircraftCatalogEntry("A333", "a333", "A330-300", "A330", seats=295, mtow_kg=242000, mlw_kg=187000, reference_fuel_flow_kgph=8800),
    "A338": AircraftCatalogEntry("A338", "a338", "A330-800neo", "A330", seats=257, mtow_kg=251000, mlw_kg=186000, reference_fuel_flow_kgph=7300),
    "A339": AircraftCatalogEntry("A339", "a339", "A330-900neo", "A330", seats=287, mtow_kg=251000, mlw_kg=191000, reference_fuel_flow_kgph=7800),

    # Airbus A340
    "A342": AircraftCatalogEntry("A342", "a342", "A340-200", "A340", seats=260, mtow_kg=275000, mlw_kg=190000, reference_fuel_flow_kgph=9800),
    "A343": AircraftCatalogEntry("A343", "a343", "A340-300", "A340", seats=295, mtow_kg=276500, mlw_kg=192000, reference_fuel_flow_kgph=10500),
    "A345": AircraftCatalogEntry("A345", "a345", "A340-500", "A340", seats=313, mtow_kg=372000, mlw_kg=240000, reference_fuel_flow_kgph=12200),
    "A346": AircraftCatalogEntry("A346", "a346", "A340-600", "A340", seats=380, mtow_kg=380000, mlw_kg=259000, reference_fuel_flow_kgph=13200),

    # Airbus A350/A380
    "A359": AircraftCatalogEntry("A359", "a359", "A350-900", "A350", seats=293, mtow_kg=280000, mlw_kg=207000, reference_fuel_flow_kgph=7200),
    "A35K": AircraftCatalogEntry("A35K", "a35k", "A350-1000", "A350", seats=350, mtow_kg=319000, mlw_kg=236000, reference_fuel_flow_kgph=8300),
    "A388": AircraftCatalogEntry("A388", "a388", "A380-800", "A380", seats=509, mtow_kg=575000, mlw_kg=394000, reference_fuel_flow_kgph=16000),

    # Boeing 737 classic / NG / MAX
    "B733": AircraftCatalogEntry("B733", "b733", "737-300", "B737", seats=140, mtow_kg=63276, mlw_kg=51710, reference_fuel_flow_kgph=3300),
    "B734": AircraftCatalogEntry("B734", "b734", "737-400", "B737", seats=159, mtow_kg=68039, mlw_kg=56245, reference_fuel_flow_kgph=3600),
    "B735": AircraftCatalogEntry("B735", "b735", "737-500", "B737", seats=122, mtow_kg=60554, mlw_kg=49900, reference_fuel_flow_kgph=3000),
    "B736": AircraftCatalogEntry("B736", "b736", "737-600", "B737", seats=123, mtow_kg=66360, mlw_kg=54657, reference_fuel_flow_kgph=3000),
    "B737": AircraftCatalogEntry("B737", "b737", "737-700", "B737", seats=140, mtow_kg=70080, mlw_kg=58059, reference_fuel_flow_kgph=3200),
    "B738": AircraftCatalogEntry("B738", "b738", "737-800", "B737", seats=189, mtow_kg=79015, mlw_kg=66360, reference_fuel_flow_kgph=3900),
    "B739": AircraftCatalogEntry("B739", "b739", "737-900", "B737", seats=215, mtow_kg=85130, mlw_kg=66360, reference_fuel_flow_kgph=4200),
    "B37M": AircraftCatalogEntry("B37M", "b37m", "737 MAX 7", "B737", seats=153, mtow_kg=80300, mlw_kg=66360, reference_fuel_flow_kgph=3200),
    "B38M": AircraftCatalogEntry("B38M", "b38m", "737 MAX 8", "B737", seats=189, mtow_kg=82190, mlw_kg=69310, reference_fuel_flow_kgph=3500),
    "B39M": AircraftCatalogEntry("B39M", "b39m", "737 MAX 9", "B737", seats=193, mtow_kg=88310, mlw_kg=74340, reference_fuel_flow_kgph=3700),
    "B3XM": AircraftCatalogEntry("B3XM", "b3xm", "737 MAX 10", "B737", seats=204, mtow_kg=89350, mlw_kg=76000, reference_fuel_flow_kgph=3900),

    # Boeing 747
    "B744": AircraftCatalogEntry("B744", "b744", "747-400", "B747", seats=416, mtow_kg=396890, mlw_kg=285763, reference_fuel_flow_kgph=14500),
    "B748": AircraftCatalogEntry("B748", "b748", "747-8", "B747", seats=467, mtow_kg=447700, mlw_kg=312000, reference_fuel_flow_kgph=13500),

    # Boeing 757/767
    "B752": AircraftCatalogEntry("B752", "b752", "757-200", "B757", seats=200, mtow_kg=115680, mlw_kg=95250, reference_fuel_flow_kgph=5100),
    "B753": AircraftCatalogEntry("B753", "b753", "757-300", "B757", seats=243, mtow_kg=124740, mlw_kg=101600, reference_fuel_flow_kgph=5700),
    "B762": AircraftCatalogEntry("B762", "b762", "767-200", "B767", seats=216, mtow_kg=159200, mlw_kg=123400, reference_fuel_flow_kgph=6500),
    "B763": AircraftCatalogEntry("B763", "b763", "767-300", "B767", seats=261, mtow_kg=186880, mlw_kg=145150, reference_fuel_flow_kgph=7200),
    "B764": AircraftCatalogEntry("B764", "b764", "767-400", "B767", seats=304, mtow_kg=204120, mlw_kg=158760, reference_fuel_flow_kgph=7800),

    # Boeing 777/787
    "B772": AircraftCatalogEntry("B772", "b772", "777-200ER", "B777", seats=317, mtow_kg=247200, mlw_kg=201800, reference_fuel_flow_kgph=8500),
    "B77L": AircraftCatalogEntry("B77L", "b77l", "777-200LR", "B777", seats=317, mtow_kg=347450, mlw_kg=223168, reference_fuel_flow_kgph=9500),
    "B77W": AircraftCatalogEntry("B77W", "b77w", "777-300ER", "B777", seats=396, mtow_kg=351534, mlw_kg=251290, reference_fuel_flow_kgph=10500),
    "B77F": AircraftCatalogEntry("B77F", "b77f", "777F", "B777", seats=0, mtow_kg=347450, mlw_kg=223168, reference_fuel_flow_kgph=9500, passenger=False),
    "MD11": AircraftCatalogEntry("MD11", "md11", "MD-11", "MD11", seats=293, mtow_kg=286897, mlw_kg=199580, reference_fuel_flow_kgph=10800),
    "B788": AircraftCatalogEntry("B788", "b788", "787-8", "B787", seats=242, mtow_kg=227930, mlw_kg=172365, reference_fuel_flow_kgph=6100),
    "B789": AircraftCatalogEntry("B789", "b789", "787-9", "B787", seats=290, mtow_kg=254011, mlw_kg=192776, reference_fuel_flow_kgph=6700),
    "B78X": AircraftCatalogEntry("B78X", "b78x", "787-10", "B787", seats=330, mtow_kg=254011, mlw_kg=201849, reference_fuel_flow_kgph=7200),

    # Embraer
    "E170": AircraftCatalogEntry("E170", "e170", "E170", "EJET", seats=76, mtow_kg=37200, mlw_kg=32800, reference_fuel_flow_kgph=1800),
    "E175": AircraftCatalogEntry("E175", "e175", "E175", "EJET", seats=88, mtow_kg=40370, mlw_kg=34200, reference_fuel_flow_kgph=1900),
    "E190": AircraftCatalogEntry("E190", "e190", "E190", "EJET", seats=100, mtow_kg=51800, mlw_kg=43000, reference_fuel_flow_kgph=2400),
    "E195": AircraftCatalogEntry("E195", "e195", "E195", "EJET", seats=120, mtow_kg=52290, mlw_kg=45000, reference_fuel_flow_kgph=2600),
    "E290": AircraftCatalogEntry("E290", "e190e2", "E190-E2", "EJET", seats=106, mtow_kg=56400, mlw_kg=49000, reference_fuel_flow_kgph=2100),
    "E295": AircraftCatalogEntry("E295", "e195e2", "E195-E2", "EJET", seats=132, mtow_kg=61500, mlw_kg=54000, reference_fuel_flow_kgph=2300),

    # CRJ
    "CRJ7": AircraftCatalogEntry("CRJ7", "crj7", "CRJ700", "CRJ", seats=70, mtow_kg=34019, mlw_kg=30391, reference_fuel_flow_kgph=1700),
    "CRJ9": AircraftCatalogEntry("CRJ9", "crj9", "CRJ900", "CRJ", seats=86, mtow_kg=38330, mlw_kg=34200, reference_fuel_flow_kgph=1900),
    "CRJX": AircraftCatalogEntry("CRJX", "crjx", "CRJ1000", "CRJ", seats=100, mtow_kg=41640, mlw_kg=36740, reference_fuel_flow_kgph=2100),

    # Turboprops
    "AT43": AircraftCatalogEntry("AT43", "at43", "ATR 42", "ATR", seats=48, mtow_kg=18600, mlw_kg=18300, aircraft_category="turboprop", reference_fuel_flow_kgph=650, ci_max=50),
    "AT72": AircraftCatalogEntry("AT72", "at72", "ATR 72", "ATR", seats=70, mtow_kg=23000, mlw_kg=22350, aircraft_category="turboprop", reference_fuel_flow_kgph=850, ci_max=50),
    "DH8D": AircraftCatalogEntry("DH8D", "dh8d", "Dash 8 Q400", "DASH8", seats=78, mtow_kg=29257, mlw_kg=28000, aircraft_category="turboprop", reference_fuel_flow_kgph=1050, ci_max=50),
}


def get_catalog_entry(simbrief_or_icao_code: str) -> AircraftCatalogEntry | None:
    normalized = normalize_aircraft_code(simbrief_or_icao_code)
    if normalized is None:
        return None

    return AIRCRAFT_CATALOG.get(normalized)


def resolve_aircraft_from_title(title: str | None) -> AircraftCatalogEntry | None:
    """
    Best-effort aircraft type detection from simulator aircraft title strings.

    SimConnect TITLE is addon-specific free text ("Airbus A350-900 Qatar
    Airways", "PMDG 777-300ER Emirates", "Fenix A320"), so the normalized
    title is scanned for the longest known type token. Longest-first ordering
    matters: "A320NEO" must win over the "A320" substring it contains, and
    "777300ER" over "777200".

    Returns a catalog entry only for a clear type signal — never a guess.
    """

    normalized = normalize_aircraft_code(title)
    if normalized is None:
        return None

    direct = AIRCRAFT_CATALOG.get(normalized)
    if direct is not None:
        return direct

    for pattern, code in _TITLE_TYPE_PATTERNS:
        if pattern in normalized:
            entry = AIRCRAFT_CATALOG.get(code)
            if entry is not None:
                return entry

    return None


AIRCRAFT_CODE_ALIASES: dict[str, str] = {
    "A320200": "A320",
    "A320CEO": "A320",
    "A320NEO": "A20N",
    "A321NEO": "A21N",
    "A319NEO": "A19N",
    "A350900": "A359",
    "A3501000": "A35K",
    "A330200": "A332",
    "A330300": "A333",
    "A330900": "A339",
    "A340300": "A343",
    "A340600": "A346",
    "B737800": "B738",
    "B737700": "B737",
    "B737900": "B739",
    "B738W": "B738",
    "B737MAX7": "B37M",
    "B737MAX8": "B38M",
    "B737MAX9": "B39M",
    "B737MAX10": "B3XM",
    "B777300ER": "B77W",
    "777300ER": "B77W",
    "B777200ER": "B772",
    "777200ER": "B772",
    "B777200": "B772",
    "777200": "B772",
    "B777200LR": "B77L",
    "777200LR": "B77L",
    "B777F": "B77F",
    "777F": "B77F",
    "B7878": "B788",
    "B7879": "B789",
    "B78710": "B78X",
}


# Additional type tokens that only ever appear inside free-text simulator
# TITLE strings (addon liveries, marketing names). These are NOT code
# aliases — they are only scanned for by resolve_aircraft_from_title.
_TITLE_ONLY_PATTERNS: dict[str, str] = {
    "A3501000": "A35K",
    "A350900": "A359",
    "A350": "A359",
    "A380800": "A388",
    "A380": "A388",
    "A330800": "A338",
    "A3309": "A339",
    "A340500": "A345",
    "A340200": "A342",
    "A300600": "A306",
    "A310300": "A310",
    "7878": "B788",
    "7879": "B789",
    "78710": "B78X",
    "747400": "B744",
    "7478I": "B748",
    "7478": "B748",
    "757200": "B752",
    "757300": "B753",
    "767200": "B762",
    "767300": "B763",
    "767400": "B764",
    "737700": "B737",
    "737800": "B738",
    "737900": "B739",
    "737MAX7": "B37M",
    "737MAX8": "B38M",
    "737MAX9": "B39M",
    "737MAX10": "B3XM",
    "ATR42": "AT43",
    "ATR72": "AT72",
    "Q400": "DH8D",
    "MD11": "MD11",
}


def _build_title_type_patterns() -> tuple[tuple[str, str], ...]:
    """
    Longest-first (pattern → catalog code) table for free-text title scans.

    Longest-first is load-bearing: "A320NEO" must be tested before the
    "A320" substring it contains, and "777300ER" before "777200".
    """

    patterns: dict[str, str] = {}
    patterns.update(_TITLE_ONLY_PATTERNS)
    patterns.update(AIRCRAFT_CODE_ALIASES)
    for code in AIRCRAFT_CATALOG:
        patterns.setdefault(code, code)

    return tuple(sorted(patterns.items(), key=lambda item: (-len(item[0]), item[0])))


_TITLE_TYPE_PATTERNS: tuple[tuple[str, str], ...] = _build_title_type_patterns()


def normalize_aircraft_code(value: str | None) -> str | None:
    if value is None:
        return None

    text = str(value).strip().upper()

    if not text:
        return None

    text = (
        text.replace("-", "")
        .replace("_", "")
        .replace(" ", "")
        .replace("/", "")
    )

    return AIRCRAFT_CODE_ALIASES.get(text, text)


def easa_min_cabin_crew(seats: int) -> int:
    if seats <= 0:
        return 0

    return ceil(seats / 50)


def default_cabin_crew(seats: int, aircraft_category: str = "jet") -> int:
    """
    EASA minimum is ceil(seats / 50).

    Airline service staffing is often above legal minimum.
    For this MVP we use:
    - regional/turboprop: EASA minimum
    - short/medium-haul jet: EASA minimum + 1 when >= 150 seats
    - widebody: EASA minimum + 2
    """

    minimum = easa_min_cabin_crew(seats)

    if aircraft_category == "turboprop":
        return minimum

    if seats >= 240:
        return minimum + 2

    if seats >= 150:
        return minimum + 1

    return minimum


def typical_service_range(seats: int, aircraft_category: str = "jet") -> list[int]:
    minimum = easa_min_cabin_crew(seats)
    default = default_cabin_crew(seats, aircraft_category)

    if aircraft_category == "turboprop":
        return [minimum, max(minimum, default)]

    if seats >= 240:
        return [minimum, default + 3]

    if seats >= 150:
        return [minimum, default + 2]

    return [minimum, default + 1]


def cockpit_pilots_for_config(entry: AircraftCatalogEntry) -> int:
    return 2


def cockpit_hourly_cost_eur(entry: AircraftCatalogEntry) -> int:
    """
    MVP estimate.

    A320 baseline = 350 EUR/h per pilot.
    Widebody crews are usually more expensive; use a modest scaling.
    """

    if entry.seats >= 300:
        return 500

    if entry.seats >= 220:
        return 450

    if entry.seats >= 100:
        return 350

    return 275


def cabin_hourly_cost_eur(entry: AircraftCatalogEntry) -> int:
    if entry.seats >= 240:
        return 60

    return 50


def scaled_hourly_costs(entry: AircraftCatalogEntry) -> tuple[int, int, int]:
    """
    Returns:
      maint, own, sched

    Cost scaling from A320 baseline:
    - maintenance roughly scales with aircraft size/complexity
    - ownership scales more strongly with MTOW
    - schedule/admin cost scales mildly with seats

    This is intentionally an estimate and should be replaced by airline/VA data.
    """

    mtow = entry.mtow_kg or BASELINE_A320_MTOW_KG
    seats = entry.seats or BASELINE_A320_SEATS

    mtow_factor = mtow / BASELINE_A320_MTOW_KG
    seat_factor = seats / BASELINE_A320_SEATS

    maint = BASELINE_A320_MAINT_EUR_H * (mtow_factor ** 0.62)
    own = BASELINE_A320_OWN_EUR_H * (mtow_factor ** 0.72)
    sched = BASELINE_A320_SCHED_EUR_H * (seat_factor ** 0.45)

    return round(maint), round(own), round(sched)


def reference_fuel_flow(entry: AircraftCatalogEntry) -> int:
    if entry.reference_fuel_flow_kgph is not None:
        return entry.reference_fuel_flow_kgph

    mtow = entry.mtow_kg or BASELINE_A320_MTOW_KG
    mtow_factor = mtow / BASELINE_A320_MTOW_KG

    return round(BASELINE_A320_FF_KGPH * (mtow_factor ** 0.68))
