"""
scraper/seed_swuguru_players.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Builds the player database in `swuguru` from the HRI.gg public API.

Strategy:
  1. Walk /api/v1/rankings for each format (premier, limited) — bulk data,
     47 pages × 200 rows — to populate players + player_ratings.
  2. Optionally enrich individual profiles (--enrich) to fill in
     country, team, aliases, avatar, links.  Skipped by default since
     most fields are currently null and it costs ~9k extra requests.

Tables created:
  players         — one row per HRI slug (stable melee handle)
  player_ratings  — one row per (slug, format) with Glicko-2 stats
  player_aliases  — known alternate names for a player

Usage:
    python3 /app/scraper/seed_swuguru_players.py
    python3 /app/scraper/seed_swuguru_players.py --enrich   # fetch individual profiles too
    python3 /app/scraper/seed_swuguru_players.py --formats premier limited
"""

import argparse
import logging
import os
import sys
import time

import httpx
import psycopg2
import psycopg2.extras
from tenacity import retry, stop_after_attempt, wait_exponential
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

API_BASE = "https://hri.gg/api/v1"
HEADERS  = {
    "Accept":     "application/json",
    "User-Agent": "SWUGuru/1.0 (analytics; contact alecneukirch@gmail.com)",
}
PAGE_SIZE   = 200
PAGE_DELAY  = 0.4   # between ranking pages
ENRICH_DELAY = 0.3  # between individual profile requests

DDL = """
CREATE TABLE IF NOT EXISTS players (
    slug            TEXT        PRIMARY KEY,
    melee_username  TEXT,
    display_name    TEXT,
    country         TEXT,
    verified        BOOLEAN,
    avatar_url      TEXT,
    current_team    TEXT,
    bio             TEXT,
    links           JSONB       NOT NULL DEFAULT '[]',
    hri_synced_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS player_ratings (
    id              SERIAL      PRIMARY KEY,
    player_slug     TEXT        NOT NULL REFERENCES players(slug) ON DELETE CASCADE,
    format          TEXT        NOT NULL,
    rating          NUMERIC(8,2),
    rd              NUMERIC(8,2),
    volatility      NUMERIC(10,8),
    peak_rating     NUMERIC(8,2),
    current_tier    TEXT,
    rank            INTEGER,
    total_events    INTEGER,
    win_rate        NUMERIC(5,2),
    last_event_delta NUMERIC(8,2),
    synced_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (player_slug, format)
);

CREATE TABLE IF NOT EXISTS player_aliases (
    id          SERIAL  PRIMARY KEY,
    player_slug TEXT    NOT NULL REFERENCES players(slug) ON DELETE CASCADE,
    alias       TEXT    NOT NULL,
    UNIQUE (player_slug, alias)
);

CREATE INDEX IF NOT EXISTS players_melee_username ON players(lower(melee_username));
CREATE INDEX IF NOT EXISTS players_country        ON players(country) WHERE country IS NOT NULL;
CREATE INDEX IF NOT EXISTS player_ratings_format  ON player_ratings(format);
CREATE INDEX IF NOT EXISTS player_ratings_tier    ON player_ratings(current_tier);
CREATE INDEX IF NOT EXISTS player_ratings_rating  ON player_ratings(rating DESC);
CREATE INDEX IF NOT EXISTS player_aliases_alias   ON player_aliases(lower(alias));
"""


def get_conn():
    return psycopg2.connect(
        host=os.getenv("DB_HOST", "192.168.1.200"),
        port=int(os.getenv("DB_PORT", 5432)),
        dbname="swuguru",
        user=os.getenv("DB_USER", "swu_user"),
        password=os.getenv("DB_PASS", "981273465aA!"),
    )


# ── HRI API ──────────────────────────────────────────────────────────────────

@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=10))
def _get(path: str, params: dict = None) -> dict:
    r = httpx.get(f"{API_BASE}{path}", params=params, headers=HEADERS, timeout=15)
    r.raise_for_status()
    return r.json()


def fetch_rankings(format: str) -> list[dict]:
    """Walk all cursor pages for a format and return flat list of ranking rows."""
    results = []
    cursor  = None
    pages   = 0

    log.info(f"Walking HRI rankings (format={format}) …")
    while True:
        params = {"format": format, "limit": PAGE_SIZE}
        if cursor:
            params["cursor"] = cursor

        data  = _get("/rankings", params)
        rows  = data.get("data", [])
        if not rows:
            break

        rank_offset = len(results)
        for i, row in enumerate(rows):
            attrs = row.get("attributes", {})
            attrs["_rank"] = rank_offset + i + 1
            results.append(attrs)

        pages  += 1
        cursor  = data.get("meta", {}).get("next_cursor")
        log.info(f"  page {pages}: {len(rows)} rows, cumulative {len(results)}")
        time.sleep(PAGE_DELAY)

        if not cursor:
            break

    log.info(f"  Done — {len(results)} players, {pages} pages")
    return results


def fetch_player_profile(slug: str) -> dict:
    """Fetch individual player profile for enrichment data."""
    try:
        data = _get(f"/players/{slug}")
        return data.get("data", {}).get("attributes", {})
    except Exception as e:
        log.debug(f"  Profile fetch failed for {slug}: {e}")
        return {}


# ── Upsert helpers ────────────────────────────────────────────────────────────

def upsert_player(cur, slug: str, attrs: dict):
    cur.execute(
        """
        INSERT INTO players (slug, melee_username, display_name, country, verified,
                             avatar_url, current_team, bio, links, hri_synced_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, now())
        ON CONFLICT (slug) DO UPDATE SET
            melee_username = COALESCE(EXCLUDED.melee_username, players.melee_username),
            display_name   = COALESCE(EXCLUDED.display_name,   players.display_name),
            country        = COALESCE(EXCLUDED.country,        players.country),
            verified       = COALESCE(EXCLUDED.verified,       players.verified),
            avatar_url     = COALESCE(EXCLUDED.avatar_url,     players.avatar_url),
            current_team   = COALESCE(EXCLUDED.current_team,   players.current_team),
            bio            = COALESCE(EXCLUDED.bio,            players.bio),
            links          = CASE WHEN EXCLUDED.links != '[]'::jsonb
                                  THEN EXCLUDED.links
                                  ELSE players.links END,
            hri_synced_at  = now()
        """,
        (
            slug,
            attrs.get("melee_username"),
            attrs.get("display_name") or attrs.get("melee_username"),
            attrs.get("country"),
            attrs.get("verified"),
            attrs.get("avatar_url"),
            attrs.get("current_team"),
            attrs.get("bio_html"),
            psycopg2.extras.Json(attrs.get("links") or []),
        ),
    )


def upsert_rating(cur, slug: str, fmt: str, attrs: dict, rank: int):
    # attrs may come from rankings walk (flat) or player profile (nested under "rating")
    rating_block = attrs.get("rating") if isinstance(attrs.get("rating"), dict) else None
    cur.execute(
        """
        INSERT INTO player_ratings
            (player_slug, format, rating, rd, volatility, peak_rating,
             current_tier, rank, total_events, win_rate, last_event_delta, synced_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now())
        ON CONFLICT (player_slug, format) DO UPDATE SET
            rating           = EXCLUDED.rating,
            rd               = EXCLUDED.rd,
            volatility       = COALESCE(EXCLUDED.volatility,       player_ratings.volatility),
            peak_rating      = EXCLUDED.peak_rating,
            current_tier     = EXCLUDED.current_tier,
            rank             = EXCLUDED.rank,
            total_events     = COALESCE(EXCLUDED.total_events,     player_ratings.total_events),
            win_rate         = COALESCE(EXCLUDED.win_rate,         player_ratings.win_rate),
            last_event_delta = COALESCE(EXCLUDED.last_event_delta, player_ratings.last_event_delta),
            synced_at        = now()
        """,
        (
            slug,
            fmt,
            rating_block["rating"]    if rating_block else attrs.get("rating"),
            rating_block["rd"]        if rating_block else attrs.get("rd"),
            rating_block.get("volatility") if rating_block else None,
            rating_block["peak_rating"] if rating_block else attrs.get("peak_rating"),
            attrs.get("current_tier"),
            rank,
            attrs.get("total_events"),
            attrs.get("win_rate"),
            attrs.get("last_event_delta"),
        ),
    )


def upsert_aliases(cur, slug: str, aliases: list[str]):
    for alias in aliases:
        if alias and alias != slug:
            cur.execute(
                """
                INSERT INTO player_aliases (player_slug, alias)
                VALUES (%s, %s)
                ON CONFLICT (player_slug, alias) DO NOTHING
                """,
                (slug, alias),
            )


# ── Main ──────────────────────────────────────────────────────────────────────

def main(formats: list[str], enrich: bool = False):
    conn = get_conn()
    cur  = conn.cursor()

    log.info("Creating tables …")
    cur.execute(DDL)
    conn.commit()

    for fmt in formats:
        rows = fetch_rankings(fmt)

        log.info(f"Upserting {len(rows)} players for format={fmt} …")
        for attrs in rows:
            slug = attrs.get("slug")
            if not slug:
                continue
            upsert_player(cur, slug, attrs)
            upsert_rating(cur, slug, fmt, attrs, rank=attrs.get("_rank", 0))

        conn.commit()
        log.info(f"  format={fmt} committed")

    # Summary before enrichment
    cur.execute("SELECT COUNT(*) FROM players")
    n_players = cur.fetchone()[0]
    cur.execute("SELECT format, COUNT(*) FROM player_ratings GROUP BY format ORDER BY format")
    rating_counts = cur.fetchall()
    log.info(f"\nAfter bulk load — {n_players} players")
    for fmt, cnt in rating_counts:
        log.info(f"  {fmt}: {cnt} ratings")

    # Optional enrichment: fetch individual profiles
    if enrich:
        cur.execute("SELECT slug FROM players ORDER BY slug")
        slugs = [r[0] for r in cur.fetchall()]
        log.info(f"\nEnriching {len(slugs)} profiles …")

        enriched = skipped = 0
        for i, slug in enumerate(slugs, 1):
            profile = fetch_player_profile(slug)
            if not profile:
                skipped += 1
                continue

            upsert_player(cur, slug, profile)

            # Enrich 'overall' rating from profile if present
            rating_block = profile.get("rating")
            if isinstance(rating_block, dict) and rating_block.get("format") == "overall":
                upsert_rating(cur, slug, "overall", profile, rank=0)

            # Aliases
            aliases = profile.get("aliases") or []
            upsert_aliases(cur, slug, aliases)

            enriched += 1
            if i % 100 == 0:
                conn.commit()
                log.info(f"  {i}/{len(slugs)} enriched …")
            time.sleep(ENRICH_DELAY)

        conn.commit()
        log.info(f"Enrichment done — {enriched} profiles fetched, {skipped} skipped")

        cur.execute("SELECT COUNT(*) FROM players WHERE country IS NOT NULL")
        log.info(f"  Players with country: {cur.fetchone()[0]}")
        cur.execute("SELECT COUNT(*) FROM players WHERE current_team IS NOT NULL")
        log.info(f"  Players with team:    {cur.fetchone()[0]}")
        cur.execute("SELECT COUNT(*) FROM player_aliases")
        log.info(f"  Alias rows:           {cur.fetchone()[0]}")

    cur.close()
    conn.close()
    log.info("\nDone.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Seed swuguru player DB from HRI.gg")
    parser.add_argument(
        "--formats", nargs="+", default=["premier", "limited"],
        help="HRI formats to walk (default: premier limited)"
    )
    parser.add_argument(
        "--enrich", action="store_true",
        help="Fetch individual player profiles for country/team/aliases (slow — ~9k requests)"
    )
    args = parser.parse_args()
    main(formats=args.formats, enrich=args.enrich)
