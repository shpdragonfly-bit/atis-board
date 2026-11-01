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
import os
import re
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
AIRPORTS = ROOT / "airports.json"
DATA = Path(os.environ.get("ATIS_DATA_DIR") or ROOT / "data")  # the data branch checkout in Actions
LATEST = DATA / "atis.json"
STATUS = DATA / "status.json"  # written every run, so the app can tell the collector is alive
NOTIFY = ROOT / "notify.json"  # airports to send ntfy alerts for
NTFY_SERVER = os.environ.get("NTFY_SERVER") or "https://ntfy.sh"
WEBAPP_URL = "https://shpdragonfly-bit.github.io/atis-board/"
HISTORY = DATA / "history"
HISTORY_KEEP = 300          # entries kept per airport
REFRESH_EVEN_IF_SAME_MIN = 60  # rewrite unchanged data at most this often
STATE = ROOT / ".state" / "checked.json"  # last check per airport; kept in the Actions cache
TIER_MINUTES = {1: 10, 2: 30, 3: 60}      # how often each tier is fetched
DUE_SLACK = timedelta(minutes=3)           # runs start a little irregularly
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

FAA_ALL_URL = "https://datis.clowd.io/api/all"
_faa_cache: dict | None = None


def _faa_items(data) -> list[dict]:
    if isinstance(data, dict):
        return [] if data.get("error") else [data]
    return [d for d in data if isinstance(d, dict)]


def src_faa(icao: str, now: datetime) -> tuple[list[dict], str]:
    """US D-ATIS. All airports come from one /api/all call per run."""
    global _faa_cache
    if _faa_cache is None:
        _faa_cache = {}
        for item in _faa_items(json.loads(get(FAA_ALL_URL, timeout=30))):
            _faa_cache.setdefault(str(item.get("airport", "")).upper(), []).append(item)
    items = _faa_cache.get(icao)
    url = f"https://datis.clowd.io/api/{icao}"
    if items is None:  # not in the bulk list; ask for this one airport
        items = _faa_items(json.loads(get(url)))
    out = []
    for item in items:
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


METAR_URL = "https://aviationweather.gov/api/data/metar?format=json&ids="


def fetch_metars(icaos: list[str], now: datetime) -> dict[str, dict]:
    """Latest METAR per airport from the NOAA Aviation Weather Center, in batches."""
    out: dict[str, dict] = {}
    for i in range(0, len(icaos), 25):
        batch = icaos[i:i + 25]
        try:
            rows = json.loads(get(METAR_URL + ",".join(batch), timeout=30) or "[]")
        except Exception as e:
            print(f"METAR batch {batch[0]}..: {e}", file=sys.stderr)
            continue
        for r in rows if isinstance(rows, list) else []:
            icao = str(r.get("icaoId") or r.get("station_id") or "").upper()
            raw = (r.get("rawOb") or r.get("raw_text") or "").strip()
            if not icao or not raw:
                continue
            t = None
            if isinstance(r.get("obsTime"), (int, float)):
                t = datetime.fromtimestamp(r["obsTime"], timezone.utc)
            elif r.get("reportTime"):
                try:
                    t = datetime.fromisoformat(str(r["reportTime"]).replace("Z", "+00:00").replace(" ", "T"))
                    t = t if t.tzinfo else t.replace(tzinfo=timezone.utc)
                except ValueError:
                    t = None
            if t is None:  # fall back to the DDHHMMZ group in the report
                m = re.search(r"\b(\d{2})(\d{2})(\d{2})Z\b", raw)
                if m:
                    try:
                        t = now.replace(day=int(m.group(1)), hour=int(m.group(2)), minute=int(m.group(3)), second=0)
                        if t > now + timedelta(hours=1):
                            t = (t.replace(day=1) - timedelta(days=1)).replace(day=int(m.group(1)))
                    except ValueError:
                        t = None
            prev = out.get(icao)
            if prev is None or (t and prev["time"] and iso(t) > prev["time"]):
                out[icao] = {"raw": raw, "time": iso(t) if t else None}
    return out


SOURCES = {"faa": src_faa, "atisguru": src_atisguru, "hkcad": src_hkcad}
SOURCE_NAMES = {
    "faa": "FAA D-ATIS (datis.clowd.io)",
    "atisguru": "ACARS 수집 D-ATIS (atis.guru)",
    "hkcad": "홍콩 민항처 공식 ATIS",
}


# ---------------------------------------------------------------- main

def content_hash(airports: dict) -> str:
    slim = {k: [[(a["type"], a["text"]) for a in v.get("atis", [])], (v.get("metar") or {}).get("raw")]
            for k, v in airports.items()}
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


def send_alerts(airports: dict, old_airports: dict, now: datetime) -> int:
    """Push a phone alert through ntfy for every new ATIS at the airports in notify.json.

    The ntfy topic is a secret (NTFY_TOPIC), because anyone who knows it can read
    and send messages on it. Without it, nothing is sent.
    """
    topic = os.environ.get("NTFY_TOPIC", "").strip()
    if not topic:
        return 0
    try:
        cfg = json.loads(NOTIFY.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return 0
    wanted = {str(k).upper(): [t.upper() for t in v] if isinstance(v, list) else ["ARR", "DEP", "ATIS"]
              for k, v in (cfg.get("airports") or {}).items()}
    sent = 0
    for icao, types in wanted.items():
        new, prev = airports.get(icao), old_airports.get(icao)
        if not new or not prev:  # never alert on the first run for an airport
            continue
        seen = {(a["type"], a["text"]) for a in prev.get("atis", [])}
        for a in new.get("atis", []):
            if a["type"] not in types or (a["type"], a["text"]) in seen:
                continue
            issued = a.get("issued")
            if issued:
                age = now - datetime.strptime(issued, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
                if age > timedelta(minutes=90):  # a late, already-outdated report
                    continue
            kind = {"ARR": "도착", "DEP": "출발"}.get(a["type"], "통합")
            hhmm = issued[11:13] + issued[14:16] + "Z" if issued else ""
            body = {
                "topic": topic,
                "title": f"{icao} {kind} ATIS {a.get('letter') or '?'} {hhmm}".strip(),
                "message": a["text"][:3500],
                "tags": ["airplane"],
                "click": f"{WEBAPP_URL}#{icao}",
            }
            try:
                req = urllib.request.Request(NTFY_SERVER, data=json.dumps(body).encode(),
                                             headers={"Content-Type": "application/json", "User-Agent": USER_AGENT})
                urllib.request.urlopen(req, timeout=15).read()
                sent += 1
            except Exception as e:
                print(f"[{icao}] alert failed: {e}", file=sys.stderr)
    return sent


def main() -> int:
    now = now_utc()
    airports_cfg = json.loads(AIRPORTS.read_text())
    try:
        old = json.loads(LATEST.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        old = {"airports": {}}

    try:
        checked = json.loads(STATE.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        checked = {}
    force = "--all" in sys.argv

    airports: dict = {}
    failures = attempted = 0
    last_host = None
    for cfg in airports_cfg:
        icao, source = cfg["icao"], cfg["source"]
        tier = int(cfg.get("tier", 3))
        entry = {"name": cfg.get("name"), "iata": cfg.get("iata"), "region": cfg.get("region"),
                 "tier": tier, "source": source, "sourceName": SOURCE_NAMES[source],
                 "phone": cfg.get("phone", []), "links": cfg.get("links", [])}
        prev = old.get("airports", {}).get(icao)
        last = checked.get(icao) or (prev or {}).get("checkedAt")
        due = force or prev is None or not last or \
            now - datetime.strptime(last, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc) \
            >= timedelta(minutes=TIER_MINUTES.get(tier, 60)) - DUE_SLACK
        if not due:
            keep = {k: prev[k] for k in ("sourceUrl", "checkedAt", "atis", "note", "error", "errorAt", "metar") if k in prev}
            entry.update(keep)
            airports[icao] = entry
            continue
        if last_host == source == "atisguru":
            time.sleep(2)  # be gentle with the volunteer-run site
        last_host = source
        attempted += 1
        try:
            items, url = SOURCES[source](icao, now)
            entry.update({"sourceUrl": url, "checkedAt": iso(now), "atis": items})
            checked[icao] = iso(now)
            if not items:
                entry["note"] = "현재 제공되는 ATIS 문자가 없습니다."
        except Exception as e:  # keep the last good data on failure
            failures += 1
            prev = prev or {}
            entry.update({"sourceUrl": prev.get("sourceUrl"), "checkedAt": prev.get("checkedAt"),
                          "atis": prev.get("atis", []), "error": f"{type(e).__name__}: {e}"[:200],
                          "errorAt": iso(now)})
            print(f"[{icao}] {source} failed: {e}", file=sys.stderr)
        airports[icao] = entry
        update_history(icao, entry["atis"], now)
        print(f"[{icao}] {len(entry['atis'])} ATIS" + (" (error, kept previous)" if "error" in entry else ""))

    metars = fetch_metars([c["icao"] for c in airports_cfg], now)
    for icao, entry in airports.items():
        if icao in metars:
            entry["metar"] = metars[icao]
        elif (old.get("airports", {}).get(icao) or {}).get("metar"):
            entry["metar"] = old["airports"][icao]["metar"]
    print(f"METAR for {len(metars)} airports")
    print(f"alerts sent: {send_alerts(airports, old.get('airports', {}), now)}")

    STATE.parent.mkdir(exist_ok=True)
    STATE.write_text(json.dumps(checked, indent=1) + "\n")
    print(f"fetched {attempted} of {len(airports_cfg)} airports this run")

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
    DATA.mkdir(exist_ok=True)
    STATUS.write_text(json.dumps({"lastRun": iso(now), "fetched": attempted, "failures": failures,
                                  "metar": len(metars)}) + "\n")
    return 0 if attempted == 0 or failures < attempted else 1


if __name__ == "__main__":
    sys.exit(main())
