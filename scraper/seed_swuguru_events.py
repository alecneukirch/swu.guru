"""
scraper/seed_swuguru_events.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Creates the `events` table in `swuguru` and populates it from the
swu-competitivehub.com tournament results page.

Fetches the hub list page, then for each event fetches the hub detail
page to get the Melee tournament ID.  Idempotent upsert on hub_url.

Usage:
    python3 /app/scraper/seed_swuguru_events.py                # ASH premier PQs
    python3 /app/scraper/seed_swuguru_events.py --set ASH      # explicit set
    python3 /app/scraper/seed_swuguru_events.py --url "https://..."  # custom hub URL
    python3 /app/scraper/seed_swuguru_events.py --limit 5      # first N events only
"""

import argparse
import logging
import os
import re
import sys
import time

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

sys.path.insert(0, "/app")
from scraper.melee import get as hub_get, HUB_BASE

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

DELAY = 0.5   # seconds between hub detail requests

DDL = """
CREATE TABLE IF NOT EXISTS events (
    id           SERIAL      PRIMARY KEY,
    hub_url      TEXT        UNIQUE,
    melee_id     TEXT        UNIQUE,
    melee_url    TEXT,
    name         TEXT        NOT NULL,
    date         DATE,
    set_code     TEXT        REFERENCES sets(code),
    format       TEXT,
    event_level  TEXT,
    venue        TEXT,
    country      TEXT,
    player_count INTEGER,
    winner       TEXT,
    scraped_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS events_set_code   ON events(set_code);
CREATE INDEX IF NOT EXISTS events_date       ON events(date);
CREATE INDEX IF NOT EXISTS events_melee_id   ON events(melee_id);
CREATE INDEX IF NOT EXISTS events_format     ON events(format);
"""


def get_conn():
    return psycopg2.connect(
        host=os.getenv("DB_HOST", "192.168.1.200"),
        port=int(os.getenv("DB_PORT", 5432)),
        dbname="swuguru",
        user=os.getenv("DB_USER", "swu_user"),
        password=os.getenv("DB_PASS", "981273465aA!"),
    )


# ── Hub scraping ──────────────────────────────────────────────────────────────

def scrape_hub_list(url: str, set_filter: str = None) -> list[dict]:
    """Return event stubs from the hub results table."""
    log.info(f"Fetching hub list: {url}")
    soup = hub_get(url)
    table = soup.find("table")
    if not table:
        log.error("No table found on hub results page")
        return []

    events = []
    for row in table.find_all("tr")[1:]:
        cells = row.find_all("td")
        if len(cells) < 6:
            continue
        a = cells[1].find("a")
        if not a:
            continue

        set_txt   = cells[6].get_text(strip=True) if len(cells) > 6 else ""
        level_txt = cells[4].get_text(strip=True)

        if set_filter and set_txt.upper() != set_filter.upper():
            continue

        players_txt = cells[5].get_text(strip=True)
        events.append({
            "name":         a.get_text(strip=True),
            "date":         cells[0].get_text(strip=True) or None,
            "hub_url":      a["href"],
            "winner":       cells[2].get_text(strip=True) or None,
            "country":      cells[3].get_text(strip=True) or None,
            "event_level":  level_txt or None,
            "player_count": int(players_txt) if players_txt.isdigit() else None,
            "set_code":     set_txt or None,
        })

    log.info(f"  {len(events)} events found")
    return events


def scrape_hub_detail(hub_url: str) -> dict:
    """Fetch hub event detail page → melee_id, venue, etc."""
    soup    = hub_get(hub_url)
    text    = soup.get_text(" ", strip=True)
    result  = {}

    h1 = soup.find("h1")
    result["name"] = h1.get_text(strip=True) if h1 else ""

    a = soup.find("a", href=re.compile(r"melee\.gg/Tournament/View/(\d+)"))
    if a:
        result["melee_url"] = a["href"]
        m = re.search(r"/(\d+)$", a["href"])
        result["melee_id"] = m.group(1) if m else None
    else:
        result["melee_id"]  = None
        result["melee_url"] = None

    m = re.search(r"Location[:\s]+(.+?)(?:Structure:|$)", text, re.DOTALL)
    if m:
        raw   = re.sub(r"\s+", " ", m.group(1)).strip()
        parts = [p.strip() for p in re.split(r"\s*/\s*", raw) if p.strip()]
        result["venue"] = parts[0] if parts else None
    else:
        result["venue"] = None

    return result


# ── DB ────────────────────────────────────────────────────────────────────────

def upsert_event(cur, ev: dict):
    cur.execute(
        """
        INSERT INTO events
            (hub_url, melee_id, melee_url, name, date, set_code, format,
             event_level, venue, country, player_count, winner, scraped_at)
        VALUES
            (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now())
        ON CONFLICT (hub_url) DO UPDATE SET
            melee_id     = COALESCE(EXCLUDED.melee_id,     events.melee_id),
            melee_url    = COALESCE(EXCLUDED.melee_url,    events.melee_url),
            name         = EXCLUDED.name,
            date         = COALESCE(EXCLUDED.date,         events.date),
            set_code     = COALESCE(EXCLUDED.set_code,     events.set_code),
            format       = COALESCE(EXCLUDED.format,       events.format),
            event_level  = COALESCE(EXCLUDED.event_level,  events.event_level),
            venue        = COALESCE(EXCLUDED.venue,        events.venue),
            country      = COALESCE(EXCLUDED.country,      events.country),
            player_count = COALESCE(EXCLUDED.player_count, events.player_count),
            winner       = COALESCE(EXCLUDED.winner,       events.winner),
            scraped_at   = now()
        RETURNING id, (xmax = 0) AS inserted
        """,
        (
            ev.get("hub_url"),
            ev.get("melee_id"),
            ev.get("melee_url"),
            ev["name"],
            ev.get("date"),
            ev.get("set_code"),
            ev.get("format"),
            ev.get("event_level"),
            ev.get("venue"),
            ev.get("country"),
            ev.get("player_count"),
            ev.get("winner"),
        ),
    )
    row = cur.fetchone()
    return row[0], row[1]   # id, was_inserted


# ── Main ──────────────────────────────────────────────────────────────────────

def main(hub_url: str, set_filter: str = None, fmt: str = "premier", limit: int = 0):
    conn = get_conn()
    cur  = conn.cursor()
    cur.execute(DDL)
    conn.commit()

    stubs = scrape_hub_list(hub_url, set_filter=set_filter)
    if limit:
        stubs = stubs[:limit]

    inserted = updated = failed = 0

    for i, stub in enumerate(stubs, 1):
        hub_url_detail = stub["hub_url"]
        log.info(f"[{i}/{len(stubs)}] {stub['name']} ({stub.get('date','?')}) — {stub.get('country','')}")
        try:
            detail = scrape_hub_detail(hub_url_detail)
            time.sleep(DELAY)

            ev = {**stub, **detail, "format": fmt}
            # hub detail name is more reliable than list name
            if detail.get("name"):
                ev["name"] = detail["name"]

            eid, was_inserted = upsert_event(cur, ev)
            conn.commit()

            if was_inserted:
                log.info(f"  + inserted (id={eid}, melee_id={ev.get('melee_id')})")
                inserted += 1
            else:
                log.info(f"  ~ updated  (id={eid}, melee_id={ev.get('melee_id')})")
                updated += 1

        except Exception as e:
            conn.rollback()
            log.error(f"  Failed: {e}")
            failed += 1

    cur.execute("SELECT COUNT(*) FROM events")
    total = cur.fetchone()[0]
    log.info(
        f"\nDone — {inserted} inserted, {updated} updated, {failed} failed "
        f"({total} total events in DB)"
    )
    cur.close()
    conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Seed swuguru events from hub")
    parser.add_argument("--url",   default="https://www.swu-competitivehub.com/tournaments-results/?range=3&category=premier",
                        help="Hub results URL")
    parser.add_argument("--set",   default="ASH",   help="Set code filter (default: ASH)")
    parser.add_argument("--format", default="premier", help="Format label to store (default: premier)")
    parser.add_argument("--limit", type=int, default=0, help="Max events to process (0=all)")
    args = parser.parse_args()
    main(hub_url=args.url, set_filter=args.set, fmt=args.format, limit=args.limit)
