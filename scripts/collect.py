#!/usr/bin/env python3
"""Collect ATIS text for the airports in airports.json and write data/atis.json.

Sources
  faa      FAA D-ATIS via datis.clowd.io (US airports)
  hkcad    Hong Kong Civil Aviation Department public ATIS page (VHHH)
  atisguru atis.guru, D-ATIS collected from ACARS (only updated when some
           aircraft requests the ATIS, so it can be hours or days old)

The issue time of every ATIS is taken from the HHMMZ written in the text
itself, never from the time a site says it received it.

Standard library only. Unofficial, not for operational use.
"""

from __future__ import annotations

import hashlib
import html
import json
import re
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
AIRPORTS = ROOT / "airports.json"
DATA = ROOT / "data"
LATEST = DATA / "atis.json"
HISTORY = DATA / "history"
HISTORY_KEEP = 300          # entries kept per airport
REFRESH_EVEN_IF_SAME_MIN = 60  # rewrite unchanged data at most this often
USER_AGENT = "atis-board/1.0 (personal, low-frequency; +https://github.com/shpdragonfly-bit/atis-board)"


# ---------------------------------------------------------------- helpers

def now_utc() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def iso(dt: datetime | None) -> str | None:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ") if dt else None


def get(url: str, timeout: int = 20) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode(r.headers.get_content_charset() or "utf-8", errors="replace")


def clean_lines(text: str) -> str:
    text = text.replace("\r", "\n").replace("\t", " ")
    lines = [re.sub(r" {2,}", " ", ln).strip() for ln in text.split("\n")]
    return "\n".join(ln for ln in lines if ln)


def html_to_lines(page: str) -> list[str]:
    page = re.sub(r"(?is)<(script|style|svg)\b.*?</\1>", " ", page)
    page = re.sub(r"(?i)<br\s*/?>|</(p|div|tr|h\d|li|td|pre)>", "\n", page)
    page = re.sub(r"<[^>]+>", " ", page)
    return [ln for ln in clean_lines(html.unescape(page)).split("\n")]


def issue_time(text: str, ref: datetime) -> datetime | None:
    """Latest datetime matching the HHMM(Z) in the text that is not after ref (+5 min)."""
    t = " ".join(text.upper().split())
    m = (re.search(r"\b([01]\d|2[0-3])([0-5]\d)Z\b", t)
         or re.search(r"\bTIME ([01]\d|2[0-3])([0-5]\d)\b", t))
    if not m:
        return None
    dt = ref.replace(hour=int(m.group(1)), minute=int(m.group(2)), second=0)
    if dt > ref + timedelta(minutes=5):
        dt -= timedelta(days=1)
    return dt


def info_letter(text: str) -> str | None:
    t = " ".join(text.upper().split())
    m = (re.search(r"\bATIS (?:INFO(?:RMATION)? )?([A-Z])\b", t)
         or re.search(r"\bINFO(?:RMATION)? ([A-Z])\b", t))
    return m.group(1) if m else None


def kind_of(text: str, fallback: str = "ATIS") -> str:
    head = " ".join(text.upper().split())[:60]
    if re.search(r"\b(ARR|ARRIVAL)\b", head):
        return "ARR"
    if re.search(r"\b(DEP|DEPARTURE)\b", head):
        return "DEP"
    return fallback


def record(kind: str, text: str, ref: datetime, **extra) -> dict:
    text = clean_lines(text)
    rec = {"type": kind, "letter": info_letter(text), "issued": iso(issue_time(text, ref)), "text": text}
    rec.update({k: v for k, v in extra.items() if v})
    return rec


# ---------------------------------------------------------------- sources

def src_faa(icao: str, now: datetime) -> tuple[list[dict], str]:
    url = f"https://datis.clowd.io/api/{icao}"
    data = json.loads(get(url))
    if isinstance(data, dict):
        if data.get("error"):
            return [], url
        data = [data]
    out = []
    for item in data:
        text = item.get("datis") or ""
        if not text:
            continue
        kind = {"arr": "ARR", "dep": "DEP"}.get(str(item.get("type", "")).lower(), "ATIS")
        out.append(record(kind, text, now))
    return out, url


_GURU_CARD = re.compile(
    r'<h5 class="card-title"[^>]*>\s*(Arrival|Departure|Combined)?\s*ATIS\s*</h5>\s*'
    r"<h6([^>]*)>([^<]*)</h6>\s*"
    r'<div class="atis">(.*?)</div>',
    re.S,
)


def src_atisguru(icao: str, now: datetime) -> tuple[list[dict], str]:
    url = f"https://atis.guru/atis/{icao}"
    page = get(url)
    out = []
    for label, attrs, received, body in _GURU_CARD.findall(page):
        text = html.unescape(body)
        recv = None
        m = re.match(r"\s*(\d{4}-\d{2}-\d{2} \d{2}:\d{2}) UTC", received)
        if m:
            recv = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
        by = re.search(r'title="([^"]*)"', attrs)
        kind = {"Arrival": "ARR", "Departure": "DEP"}.get(label or "", kind_of(text))
        # atis.guru's received time can be wrong, so use whichever is earlier as reference
        ref = min(recv, now) if recv else now
        out.append(record(kind, text, ref,
                          received=iso(recv),
                          requestedBy=html.unescape(by.group(1)).replace("Requested by ", "").strip() if by else None))
    return out, url


def src_hkcad(icao: str, now: datetime) -> tuple[list[dict], str]:
    url = "https://atis.cad.gov.hk/ATIS/ATISweb/atis.php"
    lines = html_to_lines(get(url))
    out, cur = [], None
    stop = re.compile(r"^(Remarks|ARRIVAL|DEPARTURE)\b", re.I)
    for ln in lines:
        if re.match(r"^VHHH (ARR|DEP)\w* ATIS\b", ln, re.I):
            cur = [ln]
            out.append(cur)
        elif cur is not None:
            if stop.match(ln):
                cur = None
            else:
                cur.append(ln)
    return [record(kind_of(b[0]), "\n".join(b), now) for b in out], url


SOURCES = {"faa": src_faa, "atisguru": src_atisguru, "hkcad": src_hkcad}
SOURCE_NAMES = {
    "faa": "FAA D-ATIS (datis.clowd.io)",
    "atisguru": "ACARS 수집 D-ATIS (atis.guru)",
    "hkcad": "홍콩 민항처 공식 ATIS",
}


# ---------------------------------------------------------------- main

def content_hash(airports: dict) -> str:
    slim = {k: [(a["type"], a["text"]) for a in v.get("atis", [])] for k, v in airports.items()}
    return hashlib.sha256(json.dumps(slim, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def update_history(icao: str, items: list[dict], now: datetime) -> None:
    path = HISTORY / f"{icao}.json"
    try:
        hist = json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        hist = []
    seen = {(h["type"], h["text"]) for h in hist}
    added = False
    for a in items:
        if (a["type"], a["text"]) not in seen:
            hist.append({"type": a["type"], "letter": a.get("letter"), "issued": a.get("issued"),
                         "text": a["text"], "firstSeen": iso(now)})
            added = True
    if added:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(hist[-HISTORY_KEEP:], ensure_ascii=False, indent=1) + "\n")


def main() -> int:
    now = now_utc()
    airports_cfg = json.loads(AIRPORTS.read_text())
    try:
        old = json.loads(LATEST.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        old = {"airports": {}}

    airports: dict = {}
    failures = 0
    last_host = None
    for cfg in airports_cfg:
        icao, source = cfg["icao"], cfg["source"]
        if last_host == source == "atisguru":
            time.sleep(2)  # be gentle with the volunteer-run site
        last_host = source
        entry = {"name": cfg.get("name"), "region": cfg.get("region"), "source": source,
                 "sourceName": SOURCE_NAMES[source], "phone": cfg.get("phone", [])}
        try:
            items, url = SOURCES[source](icao, now)
            entry.update({"sourceUrl": url, "checkedAt": iso(now), "atis": items})
            if not items:
                entry["note"] = "현재 제공되는 ATIS 문자가 없습니다."
        except Exception as e:  # keep the last good data on failure
            failures += 1
            prev = old.get("airports", {}).get(icao, {})
            entry.update({"sourceUrl": prev.get("sourceUrl"), "checkedAt": prev.get("checkedAt"),
                          "atis": prev.get("atis", []), "error": f"{type(e).__name__}: {e}"[:200],
                          "errorAt": iso(now)})
            print(f"[{icao}] {source} failed: {e}", file=sys.stderr)
        airports[icao] = entry
        update_history(icao, entry["atis"], now)
        print(f"[{icao}] {len(entry['atis'])} ATIS" + (" (error, kept previous)" if "error" in entry else ""))

    changed = content_hash(airports) != content_hash(old.get("airports", {}))
    stale = True
    if old.get("generatedAt"):
        prev = datetime.strptime(old["generatedAt"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        stale = now - prev >= timedelta(minutes=REFRESH_EVEN_IF_SAME_MIN)
    errors_changed = {k for k, v in airports.items() if "error" in v} != \
                     {k for k, v in old.get("airports", {}).items() if "error" in v}

    if changed or stale or errors_changed:
        DATA.mkdir(exist_ok=True)
        LATEST.write_text(json.dumps({
            "generatedAt": iso(now),
            "disclaimer": "비공식 정보입니다. 운항에는 공식 ATIS(주파수·ACARS)로 확인하세요.",
            "airports": airports,
        }, ensure_ascii=False, indent=1) + "\n")
        print("atis.json written", "(content changed)" if changed else "(refresh)")
    else:
        print("no change; atis.json left as is")
    return 0 if failures < len(airports_cfg) else 1


if __name__ == "__main__":
    sys.exit(main())
