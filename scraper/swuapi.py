"""
scraper/swuapi.py
~~~~~~~~~~~~~~~~~
Syncs reference data from swuapi.com public endpoints into our local DB:
  - /export/all  →  cards + sets tables  (run once, then on new set release)
  - /metas       →  metas table          (run daily, tracks ban events)

Usage:
    python -m scraper.swuapi                  # sync everything
    python -m scraper.swuapi --cards          # cards + sets only
    python -m scraper.swuapi --metas          # metas only
    python -m scraper.swuapi --force          # ignore last-sync timestamp
"""

import argparse
import json
import logging
import time
from datetime import datetime, timezone
from typing import Optional

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

import db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)

SWUAPI_BASE = "https://api.swuapi.com"
HEADERS     = {"User-Agent": "SWUCards/1.0 (personal analytics tool)"}


# ── HTTP helpers ───────────────────────────────────────────────────────────

@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=10))
def get(path: str, params: dict = None) -> dict:
    url = SWUAPI_BASE + path
    r   = httpx.get(url, params=params, headers=HEADERS, timeout=30)
    r.raise_for_status()
    return r.json()


def get_all_pages(path: str, key: str, params: dict = None) -> list:
    """Cursor-paginate through all pages of a swuapi endpoint."""
    results = []
    cursor  = None
    params  = dict(params or {})

    while True:
        if cursor:
            params["after"] = cursor
        data   = get(path, params)
        items  = data.get(key, [])
        results.extend(items)
        log.info(f"  {path}: fetched {len(results)} so far…")

        pagination = data.get("pagination", {})
        cursor     = pagination.get("next_cursor")
        if not cursor:
            break
        time.sleep(0.3)  # be polite

    return results


# ── Sync: cards + sets ─────────────────────────────────────────────────────

def sync_cards(force: bool = False) -> int:
    if not force and already_synced_recently("cards", hours=24):
        log.info("Cards synced recently — skipping (use --force to override)")
        return 0

    log.info("Fetching full card export from swuapi…")
    data  = get("/export/all")
    cards = data.get("cards", [])
    sets  = data.get("sets",  [])
    log.info(f"  {len(cards)} cards, {len(sets)} sets")

    # Upsert sets first (cards may reference set_code)
    for s in sets:
        db.execute(
            """
            INSERT INTO sets (code, name, released_at)
            VALUES (%s, %s, %s)
            ON CONFLICT (code) DO UPDATE
               SET name = EXCLUDED.name,
                   released_at = EXCLUDED.released_at
            """,
            (s["code"], s["name"], s.get("release_date"))
        )

    # Upsert cards — Standard variant only to keep the table lean.
    # We still store all types (Unit, Event, etc.) because card name
    # lookups in decklist_cards need to resolve any card.
    inserted = 0
    for c in cards:
        # Skip non-Standard variants (Hyperspace, Foil, Showcase, etc.)
        # so we have exactly one row per printed card for image lookups.
        if c.get("variantType", "Standard") != "Standard":
            continue

        db.execute(
            """
            INSERT INTO cards (
                uuid, collector_number, name, subtitle, set_code,
                type, rarity, cost, power, hp, arena,
                aspects, traits, keywords,
                variant_type, is_leader, is_base,
                front_image_url, back_image_url, thumbnail_url,
                card_text, deploy_box, epic_action, artist, external_uid,
                synced_at
            ) VALUES (
                %s,%s,%s,%s,%s,
                %s,%s,%s,%s,%s,%s,
                %s,%s,%s,
                %s,%s,%s,
                %s,%s,%s,
                %s,%s,%s,%s,%s,
                now()
            )
            ON CONFLICT (uuid) DO UPDATE SET
                collector_number = EXCLUDED.collector_number,
                name             = EXCLUDED.name,
                subtitle         = EXCLUDED.subtitle,
                set_code         = EXCLUDED.set_code,
                type             = EXCLUDED.type,
                rarity           = EXCLUDED.rarity,
                cost             = EXCLUDED.cost,
                power            = EXCLUDED.power,
                hp               = EXCLUDED.hp,
                arena            = EXCLUDED.arena,
                aspects          = EXCLUDED.aspects,
                traits           = EXCLUDED.traits,
                keywords         = EXCLUDED.keywords,
                is_leader        = EXCLUDED.is_leader,
                is_base          = EXCLUDED.is_base,
                front_image_url  = EXCLUDED.front_image_url,
                back_image_url   = EXCLUDED.back_image_url,
                thumbnail_url    = EXCLUDED.thumbnail_url,
                card_text        = EXCLUDED.card_text,
                deploy_box       = EXCLUDED.deploy_box,
                epic_action      = EXCLUDED.epic_action,
                artist           = EXCLUDED.artist,
                external_uid     = EXCLUDED.external_uid,
                synced_at        = now()
            """,
            (
                c["uuid"],
                c.get("collector_number") or c.get("serialCode", ""),
                c["name"],
                c.get("subtitle"),
                c.get("setCode"),
                c.get("type"),
                c.get("rarity"),
                c.get("cost"),
                c.get("power"),
                c.get("hp"),
                c.get("arena"),
                c.get("aspects") or [],
                c.get("traits")  or [],
                c.get("keywords") or [],
                c.get("variantType", "Standard"),
                bool(c.get("isLeader")),
                bool(c.get("isBase")),
                c.get("frontImageUrl"),
                c.get("backImageUrl"),
                c.get("thumbnailUrl"),
                c.get("text"),
                c.get("deployBox"),
                c.get("epicAction"),
                c.get("artist"),
                c.get("externalUid") or c.get("external_uid"),
            )
        )
        inserted += 1

    mark_synced("cards", inserted)
    log.info(f"Cards sync complete: {inserted} upserted")
    return inserted


# ── Sync: metas ────────────────────────────────────────────────────────────

def sync_metas(force: bool = False) -> int:
    if not force and already_synced_recently("metas", hours=6):
        log.info("Metas synced recently — skipping (use --force to override)")
        return 0

    log.info("Fetching metas from swuapi…")
    data  = get("/metas", {"format": "premiere"})
    metas = data.get("metas", [])
    log.info(f"  {len(metas)} meta eras fetched")

    upserted = 0
    for m in metas:
        db.execute(
            """
            INSERT INTO metas (
                id, name, set_code, format,
                start_date, end_date, is_current, bans, synced_at
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s::jsonb, now())
            ON CONFLICT (id) DO UPDATE SET
                name       = EXCLUDED.name,
                set_code   = EXCLUDED.set_code,
                start_date = EXCLUDED.start_date,
                end_date   = EXCLUDED.end_date,
                is_current = EXCLUDED.is_current,
                bans       = EXCLUDED.bans,
                synced_at  = now()
            """,
            (
                m["id"],
                m["name"],
                m.get("set"),
                m.get("format"),
                m.get("start"),
                m.get("end"),
                bool(m.get("isCurrent")),
                json.dumps(m.get("bans", [])),
            )
        )
        upserted += 1

    mark_synced("metas", upserted)
    log.info(f"Metas sync complete: {upserted} upserted")
    return upserted


# ── Sync state helpers ─────────────────────────────────────────────────────

def already_synced_recently(resource: str, hours: int) -> bool:
    try:
        row = db.fetchone(
            "SELECT synced_at FROM sync_state WHERE resource = %s",
            (resource,)
        )
    except Exception:
        # Table does not exist yet (schema not run) -- treat as never synced
        return False
    if not row or not row["synced_at"]:
        return False
    age = datetime.now(timezone.utc) - row["synced_at"].replace(tzinfo=timezone.utc)
    return age.total_seconds() < hours * 3600


def mark_synced(resource: str, count: int):
    try:
        db.execute(
            """
            INSERT INTO sync_state (resource, synced_at, record_count)
            VALUES (%s, now(), %s)
            ON CONFLICT (resource) DO UPDATE
               SET synced_at    = now(),
                   record_count = EXCLUDED.record_count
            """,
            (resource, count)
        )
    except Exception as e:
        log.warning(f"Could not update sync_state for {resource}: {e}")


# ── Entry point ────────────────────────────────────────────────────────────

# ── Sync: base groups ──────────────────────────────────────────────────────

def sync_base_groups(force: bool = False) -> int:
    if not force and already_synced_recently("base_groups", hours=24):
        log.info("Base groups synced recently — skipping (use --force to override)")
        return 0
    log.info("Fetching base groups from swuapi…")
    data = get("/base-groups")
    groups = data.get("base_groups", [])
    log.info(f"  {len(groups)} base groups fetched")

    upserted = 0
    for g in groups:
        name        = g.get("canonical_name", "")
        color       = (g.get("color") or "").capitalize()  # Blue, Red, Green, Yellow, White
        hp          = g.get("hp")
        rarity_cls  = g.get("rarity_class", "common")     # "common" or "named"

        # Derive ability from canonical_name prefix
        lower = name.lower()
        if lower.startswith("force "):
            ability = "force"
        elif lower.startswith("splash "):
            ability = "splash"
        elif rarity_cls == "named":
            ability = "named"
        else:
            ability = "plain"

        # rarity: named bases → Rare, common bases → Common
        rarity = "Rare" if rarity_cls == "named" else "Common"

        # label: how the group appears in the UI
        if rarity == "Rare":
            label = name
        elif ability == "plain":
            label = color or "No Aspect"
        elif ability == "force":
            label = f"{color} — Force"
        elif ability == "splash":
            label = f"{color} — Splash"
        else:
            label = name

        db.execute("""
            INSERT INTO base_reference (name, aspect, ability, label, rarity, hp)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (name) DO UPDATE SET
                aspect  = EXCLUDED.aspect,
                ability = EXCLUDED.ability,
                label   = EXCLUDED.label,
                rarity  = EXCLUDED.rarity,
                hp      = EXCLUDED.hp
        """, (name, color, ability, label, rarity, hp))
        upserted += 1

    mark_synced("base_groups", upserted)
    log.info(f"Base groups sync complete: {upserted} upserted")
    return upserted


def run(cards: bool = True, base_groups: bool = True,
        metas: bool = True, force: bool = False):
    # Check DB connectivity and schema before attempting any sync.
    # If the schema has not been run yet, log a clear message and exit.
    try:
        db.fetchone("SELECT 1 FROM cards LIMIT 1")
    except Exception as e:
        msg = str(e)
        if "does not exist" in msg or "undefined" in msg.lower():
            log.error(
                "Database schema not found. "
                "Run db_init.sql against your Postgres instance first:\n"
                "  psql -h 192.168.1.200 -U postgres -f db_init.sql"
            )
        else:
            log.error(f"Cannot connect to database: {e}")
        return

    if base_groups:
        sync_base_groups(force)
    if cards:
        sync_cards(force)
    if metas:
        sync_metas(force)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Sync reference data from swuapi.com")
    parser.add_argument("--cards",       action="store_true", help="Sync cards + sets only")
    parser.add_argument("--base-groups", action="store_true", dest="base_groups", help="Sync base groups only")
    parser.add_argument("--metas",       action="store_true", help="Sync metas only")
    parser.add_argument("--force",       action="store_true", help="Ignore last-sync timestamp")
    args = parser.parse_args()

    # If no specific flags given, sync everything
    sync_all = not (args.cards or args.metas or args.base_groups)

    run(
        cards       = sync_all or args.cards,
        base_groups = sync_all or args.base_groups,
        metas       = sync_all or args.metas,
        force       = args.force,
    )
