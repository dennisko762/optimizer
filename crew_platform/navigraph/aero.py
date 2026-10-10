"""Parsers for aeronautical briefing data: NOTAM, NAT tracks, risks.

Scope note — this is deliberate, not an omission:

Navigraph's public API covers charts (Jeppesen), enroute tiles and FMS
data. It does **not** serve NOTAMs, SIGMETs, operational risk bulletins
or the NAT track message. Those come from an operator-supplied feed
(ICAO-format NOTAM service, NAT TRACK MESSAGE source, company risk
bulletin) configured through NAVIGRAPH_NOTAM_URL / NAVIGRAPH_RISK_URL.

The parsers below therefore accept BOTH shapes:
- structured JSON records (what a modern NOTAM API returns), and
- the raw ICAO text block (what an AIS/NOTAM office emits),

and normalise them into the view models the Qatar screens render. Every
parser is total: malformed records are skipped, never raised, because a
single bad NOTAM must not blank the briefing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

# ---------------------------------------------------------------------------
# NOTAM
# ---------------------------------------------------------------------------

#: NOTAM Q-code subject/condition prefixes that matter operationally.
#: Used to classify severity for the crew-desk badge.
_CRITICAL_QCODES = ("QMRLC", "QMXLC", "QFALC", "QLRAS", "QMRLT")
_RUNWAY_QCODES = ("QMR", "QMX", "QMS")

_ICAO_RE = re.compile(r"^[A-Z]{4}$")
_NOTAM_ID_RE = re.compile(r"\b([A-Z])(\d{4})/(\d{2})\b")
_Q_LINE_RE = re.compile(r"\bQ\)\s*([^A-Z)]*?[A-Z]{4})?/?(Q[A-Z]{4})?")
_FIELD_RE = re.compile(r"\b([A-GQ])\)\s*", re.MULTILINE)
_DT_RE = re.compile(r"^(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})")


@dataclass(frozen=True)
class Notam:
    """One normalised NOTAM."""

    id: str
    icao: str
    text: str
    q_code: Optional[str] = None
    start: Optional[str] = None  # ISO-8601 UTC
    end: Optional[str] = None  # ISO-8601 UTC, or None for PERM
    permanent: bool = False
    severity: str = "info"  # info | caution | critical
    source: str = "feed"
    raw: Optional[str] = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "icao": self.icao,
            "text": self.text,
            "q_code": self.q_code,
            "start": self.start,
            "end": self.end,
            "permanent": self.permanent,
            "severity": self.severity,
            "source": self.source,
        }


def _classify(q_code: Optional[str], text: str) -> str:
    code = (q_code or "").upper()
    if code and any(code.startswith(c) for c in _CRITICAL_QCODES):
        return "critical"
    if code and any(code.startswith(c) for c in _RUNWAY_QCODES):
        return "caution"
    upper = text.upper()
    if "CLOSED" in upper or "CLSD" in upper or "U/S" in upper:
        return "critical"
    if "WIP" in upper or "LIMITED" in upper or "RESTRICTED" in upper:
        return "caution"
    return "info"


def _parse_notam_datetime(token: str) -> Optional[str]:
    """Convert a NOTAM YYMMDDHHMM stamp to ISO-8601 UTC."""
    token = (token or "").strip().upper()
    m = _DT_RE.match(token)
    if not m:
        return None
    yy, mm, dd, hh, mi = (int(g) for g in m.groups())
    if not (1 <= mm <= 12 and 1 <= dd <= 31 and hh <= 23 and mi <= 59):
        return None
    try:
        dt = datetime(2000 + yy, mm, dd, hh, mi, tzinfo=timezone.utc)
    except ValueError:
        return None
    return dt.isoformat().replace("+00:00", "Z")


def parse_icao_notam(raw: str, fallback_icao: str = "") -> Optional[Notam]:
    """Parse one raw ICAO-format NOTAM block.

    Handles the standard field layout::

        A1234/25 NOTAMN
        Q) OTDF/QMRLC/IV/NBO/A/000/999/2516N05133E005
        A) OTHH B) 2510080600 C) 2510081800
        E) RWY 16L/34R CLOSED DUE WIP
    """
    if not raw or not raw.strip():
        return None
    text = " ".join(raw.split())

    id_match = _NOTAM_ID_RE.search(text)
    notam_id = (
        f"{id_match.group(1)}{id_match.group(2)}/{id_match.group(3)}"
        if id_match
        else ""
    )

    # Split on single-letter field markers "A) ", "B) " ...
    fields: dict[str, str] = {}
    marks = list(_FIELD_RE.finditer(text))
    for idx, mark in enumerate(marks):
        key = mark.group(1)
        start = mark.end()
        end = marks[idx + 1].start() if idx + 1 < len(marks) else len(text)
        fields.setdefault(key, text[start:end].strip())

    q_field = fields.get("Q", "")
    q_code = None
    for part in q_field.split("/"):
        part = part.strip().upper()
        if len(part) == 5 and part.startswith("Q"):
            q_code = part
            break

    icao = (fields.get("A") or "").split()[0].upper() if fields.get("A") else ""
    if not _ICAO_RE.match(icao):
        icao = (fallback_icao or "").upper()

    body = fields.get("E") or ""
    if not body:
        # No E) field — use everything after the header as the body.
        body = text[id_match.end():].strip() if id_match else text

    end_field = (fields.get("C") or "").strip().upper()
    permanent = end_field.startswith("PERM")

    if not notam_id and not body:
        return None

    return Notam(
        id=notam_id or f"{icao or 'UNKN'}-{abs(hash(body)) % 100000:05d}",
        icao=icao,
        text=body,
        q_code=q_code,
        start=_parse_notam_datetime(fields.get("B", "")),
        end=None if permanent else _parse_notam_datetime(end_field),
        permanent=permanent,
        severity=_classify(q_code, body),
        source="icao-text",
        raw=raw.strip(),
    )


def _notam_from_record(record: dict[str, Any]) -> Optional[Notam]:
    """Normalise a structured JSON NOTAM record."""
    if not isinstance(record, dict):
        return None
    # Raw ICAO text embedded in a JSON envelope is common; prefer it,
    # then overlay any explicit structured fields.
    raw = (
        record.get("raw")
        or record.get("rawText")
        or record.get("icaoMessage")
        or record.get("message")
    )
    base: Optional[Notam] = None
    if isinstance(raw, str) and ")" in raw:
        base = parse_icao_notam(raw, str(record.get("icao") or ""))

    text = (
        record.get("text")
        or record.get("body")
        or record.get("notamText")
        or (base.text if base else None)
        or (raw if isinstance(raw, str) else "")
    )
    icao = str(
        record.get("icao")
        or record.get("location")
        or record.get("airport")
        or (base.icao if base else "")
    ).upper()
    notam_id = str(
        record.get("id")
        or record.get("notamId")
        or record.get("number")
        or (base.id if base else "")
    )
    if not text and not notam_id:
        return None
    q_code = record.get("q_code") or record.get("qCode") or (base.q_code if base else None)
    permanent = bool(record.get("permanent") or (base.permanent if base else False))
    return Notam(
        id=notam_id or f"{icao or 'UNKN'}-{abs(hash(text)) % 100000:05d}",
        icao=icao,
        text=str(text),
        q_code=str(q_code).upper() if q_code else None,
        start=record.get("start") or record.get("effectiveStart") or (base.start if base else None),
        end=None
        if permanent
        else (record.get("end") or record.get("effectiveEnd") or (base.end if base else None)),
        permanent=permanent,
        severity=str(record.get("severity") or _classify(q_code, str(text))),
        source=str(record.get("source") or "feed"),
        raw=raw if isinstance(raw, str) else (base.raw if base else None),
    )


def parse_notam_records(payload: Any, icao_filter: Optional[Iterable[str]] = None) -> list[Notam]:
    """Parse any supported NOTAM payload into normalised records.

    Accepts a list of records, a ``{"notams": [...]}`` envelope, a
    ``{"ICAO": [...]}`` map, or a raw multi-NOTAM text block. Returns
    critical NOTAMs first, then caution, then info — the order the crew
    desk displays them in.
    """
    wanted = {str(i).upper() for i in icao_filter} if icao_filter else None
    records: list[Notam] = []

    def ingest(obj: Any, hint: str = "") -> None:
        if obj is None:
            return
        if isinstance(obj, str):
            # Raw text: NOTAM blocks are separated by blank lines.
            for block in re.split(r"\n\s*\n", obj.strip()):
                parsed = parse_icao_notam(block, hint)
                if parsed:
                    records.append(parsed)
            return
        if isinstance(obj, list):
            for item in obj:
                ingest(item, hint)
            return
        if isinstance(obj, dict):
            for key in ("notams", "items", "data", "results"):
                if key in obj:
                    ingest(obj[key], hint)
                    return
            if any(k in obj for k in ("text", "body", "raw", "rawText", "message",
                                      "notamText", "id", "notamId")):
                parsed = _notam_from_record(obj)
                if parsed:
                    records.append(parsed)
                return
            # Treat as an ICAO -> notams map.
            for key, value in obj.items():
                ingest(value, str(key))

    ingest(payload)

    if wanted:
        records = [n for n in records if not n.icao or n.icao in wanted]

    rank = {"critical": 0, "caution": 1, "info": 2}
    records.sort(key=lambda n: (rank.get(n.severity, 3), n.icao, n.id))
    return records


def notam_summary(notams: Iterable[Notam]) -> dict[str, Any]:
    """Counts per severity + affected stations, for the inbox badge."""
    items = list(notams)
    counts = {"critical": 0, "caution": 0, "info": 0}
    stations: set[str] = set()
    for n in items:
        counts[n.severity] = counts.get(n.severity, 0) + 1
        if n.icao:
            stations.add(n.icao)
    return {
        "total": len(items),
        "counts": counts,
        "stations": sorted(stations),
        "station_count": len(stations),
    }


# ---------------------------------------------------------------------------
# NAT tracks
# ---------------------------------------------------------------------------

_NAT_TMI_RE = re.compile(r"\bTMI\s*(?:IS\s*)?(\d{3})\b")
# Validity windows appear as "OCT 07/1130Z TO OCT 07/1900Z" or
# "071130 TO 071900" — allow the trailing Z and an intervening month name.
_NAT_VALID_DAY_RE = re.compile(
    r"\b(\d{2})/(\d{4})Z?\s*(?:TO|-|UNTIL)\s*(?:[A-Z]{3}\s+)?(\d{2})/(\d{4})Z?\b"
)
_NAT_VALID_STAMP_RE = re.compile(r"\b(\d{6})Z?\s*(?:TO|-|UNTIL)\s*(\d{6})Z?\b")
_NAT_TRACK_LINE_RE = re.compile(r"^\s*([A-Z])\s+([A-Z0-9/\s]{8,})$")


@dataclass(frozen=True)
class NatTrack:
    """One North Atlantic organised track."""

    name: str
    direction: str  # WESTBOUND | EASTBOUND | UNKNOWN
    valid: str
    track: str
    levels: list[str] = field(default_factory=list)
    tmi: Optional[str] = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "direction": self.direction,
            "valid": self.valid,
            "track": self.track,
            "levels": list(self.levels),
            "tmi": self.tmi,
        }


def parse_nat_track_message(message: str) -> list[NatTrack]:
    """Parse a NAT TRACK MESSAGE (NAT-1 / NAT-2 bulletin).

    Westbound tracks are lettered A-Z and valid for the daytime window;
    eastbound tracks are lettered from the end of the alphabet (S-Z) at
    night. Direction is taken from the explicit header when present and
    inferred from the window otherwise.
    """
    if not message or not message.strip():
        return []

    tmi_match = _NAT_TMI_RE.search(message.upper())
    tmi = tmi_match.group(1) if tmi_match else None

    upper = message.upper()
    direction = "UNKNOWN"
    if "EASTBOUND" in upper or "EAST BOUND" in upper:
        direction = "EASTBOUND"
    elif "WESTBOUND" in upper or "WEST BOUND" in upper:
        direction = "WESTBOUND"

    valid = ""
    dm = _NAT_VALID_DAY_RE.search(upper)
    if dm:
        valid = f"{dm.group(1)}/{dm.group(2)}Z-{dm.group(3)}/{dm.group(4)}Z"
    else:
        sm = _NAT_VALID_STAMP_RE.search(upper)
        if sm:
            valid = f"{sm.group(1)}Z-{sm.group(2)}Z"

    tracks: list[NatTrack] = []
    for line in upper.splitlines():
        line = line.rstrip()
        if not line or line.startswith(("NAT TRACK", "REMARKS", "END OF")):
            continue
        m = _NAT_TRACK_LINE_RE.match(line)
        if not m:
            continue
        letter, body = m.group(1), " ".join(m.group(2).split())
        # Trailing flight levels, e.g. "... RAFIN 350 360 370 380"
        parts = body.split()
        levels = [p for p in parts if len(p) == 3 and p.isdigit() and 200 <= int(p) <= 450]
        waypoints = [p for p in parts if p not in levels]
        if len(waypoints) < 2:
            continue
        tracks.append(
            NatTrack(
                name=f"NAT {letter}",
                direction=direction,
                valid=valid,
                track=" ".join(waypoints),
                levels=[f"FL{lvl}" for lvl in levels],
                tmi=tmi,
            )
        )
    return tracks


# ---------------------------------------------------------------------------
# Operational risk bulletins
# ---------------------------------------------------------------------------

_RISK_LEVEL_RE = re.compile(r"LEVEL\s*([1-5])", re.IGNORECASE)


@dataclass(frozen=True)
class OperationalRisk:
    """One operational-risk notice (airspace warning or country level)."""

    region: str
    status: str
    kind: str  # airspace | operator
    level: Optional[int] = None
    updated: Optional[str] = None
    source: str = "feed"
    detail: Optional[str] = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "region": self.region,
            "status": self.status,
            "kind": self.kind,
            "level": self.level,
            "updated": self.updated,
            "source": self.source,
            "detail": self.detail,
        }


def parse_risk_records(payload: Any) -> list[OperationalRisk]:
    """Normalise an operational-risk feed.

    Accepted shapes: a list of records, ``{"risks": [...]}``, or
    ``{"airspace": [...], "operator": [...]}``. Unknown fields are
    ignored; records without a region are dropped.
    """
    out: list[OperationalRisk] = []

    def one(record: Any, kind_hint: str = "airspace") -> None:
        if not isinstance(record, dict):
            return
        region = (
            record.get("region")
            or record.get("area")
            or record.get("country")
            or record.get("name")
        )
        if not region:
            return
        status = str(
            record.get("status")
            or record.get("level_label")
            or record.get("severity")
            or ""
        ).upper() or "ADVISORY"
        level = record.get("level")
        if isinstance(level, str):
            m = _RISK_LEVEL_RE.search(level)
            level_num = int(m.group(1)) if m else None
            if not record.get("status"):
                status = level.upper()
        elif isinstance(level, (int, float)):
            level_num = int(level)
        else:
            level_num = None
        kind = str(record.get("kind") or kind_hint).lower()
        out.append(
            OperationalRisk(
                region=str(region).upper(),
                status=status,
                kind="operator" if kind.startswith("oper") else "airspace",
                level=level_num,
                updated=record.get("updated") or record.get("issued"),
                source=str(record.get("source") or "feed"),
                detail=record.get("detail") or record.get("text"),
            )
        )

    if isinstance(payload, list):
        for item in payload:
            one(item)
    elif isinstance(payload, dict):
        handled = False
        for key, hint in (("airspace", "airspace"), ("operator", "operator"),
                          ("operator_risks", "operator"), ("official_notices", "airspace")):
            if isinstance(payload.get(key), list):
                handled = True
                for item in payload[key]:
                    one(item, hint)
        for key in ("risks", "items", "data", "results"):
            if isinstance(payload.get(key), list):
                handled = True
                for item in payload[key]:
                    one(item)
        if not handled:
            one(payload)

    out.sort(key=lambda r: (r.kind != "airspace", -(r.level or 0), r.region))
    return out
