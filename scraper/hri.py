"""
scraper/hri.py
~~~~~~~~~~~~~~
Syncs HRI (Holocron Rating Index) premier ratings into player_identities
using the HRI public JSON API (https://hri.gg/api).

Strategy:
  Full weekly run (--force or --weekly):
    Walk /api/v1/rankings?format=premier&limit=200 cursor-paged until
    exhausted.  Upsert all ~18k players into hri_snapshot, append a row to
    hri_history (once per calendar day via ON CONFLICT DO NOTHING), then
    update player_identities by joining against hri_snapshot.

  Mid-week run (default, stale > 6 days):
    Same API walk, same snapshot/history upsert.  Then update
    player_identities by joining against hri_snapshot.  Players not found
    in the current rankings have their hri_rating_updated_at bumped without
    clearing any rating; we check hri_snapshot first so no per-player API
    hit is needed.

  New-player fast path:
    lookup_new_players() resolves recently-added player_identities that
    have never had a rating by querying hri_snapshot locally — no API
    needed.

Usage:
    python -m scraper.hri              # update stale players (>6 days old)
    python -m scraper.hri --force      # re-fetch everyone
    python -m scraper.hri --weekly     # alias for --force (weekly cron job)
    python -m scraper.hri --limit 10   # walk at most N pages (for testing)
    python -m scraper.hri --lookup     # fast-path: resolve new players from snapshot
"""

import argparse
import logging
import time

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

import db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)

API_BASE  = "https://hri.gg/api/v1"
HEADERS   = {
    "User-Agent": "SWUGuru/1.0 (analytics; contact alecneukirch@gmail.com)",
    "Accept":     "application/json",
}
PAGE_SIZE = 200
DELAY     = 2.0   # seconds between pages


# ── HTTP ───────────────────────────────────────────────────────────────────────

@retry(stop=stop_after_attempt(5), wait=wait_exponential(min=5, max=60))
def _get(path: str, params: dict = None) -> dict:
    r = httpx.get(f"{API_BASE}{path}", params=params, headers=HEADERS, timeout=15)
    if r.status_code == 429:
        retry_after = int(r.headers.get("Retry-After", 30))
        log.warning(f"  429 rate-limited — sleeping {retry_after}s")
        time.sleep(retry_after)
        r.raise_for_status()
    r.raise_for_status()
    return r.json()


# ── Rankings walk ──────────────────────────────────────────────────────────────

def fetch_all_rankings(format: str = "premier", page_limit: int = 0) -> dict[str, dict]:
    """
    Walk /api/v1/rankings cursor-paged and return a dict keyed by
    lowercased slug → {slug, display_name, rating, rd, rank}.
    """
    results: dict[str, dict] = {}
    cursor = None
    rank   = 0
    pages  = 0

    log.info(f"Walking HRI rankings (format={format}, page_size={PAGE_SIZE})…")
    while True:
        params: dict = {"format": format, "limit": PAGE_SIZE}
        if cursor is not None:
            params["cursor"] = cursor

        data = _get("/rankings", params)
        rows = data.get("data", [])
        if not rows:
            break

        for row in rows:
            rank += 1
            attrs = row.get("attributes", {})
            slug  = attrs.get("slug")
            if not slug:
                continue
            results[slug.lower()] = {
                "slug":         slug,
                "display_name": attrs.get("melee_username") or attrs.get("display_name"),
                "rating":       int(round(attrs["rating"])) if attrs.get("rating") is not None else None,
                "rd":           int(round(attrs["rd"]))     if attrs.get("rd")     is not None else None,
                "rank":         rank,
            }

        pages += 1
        log.info(f"  page {pages}: {len(rows)} rows, cumulative {len(results)} players")
        time.sleep(DELAY)

        cursor = data.get("meta", {}).get("next_cursor")
        if not cursor:
            break
        if page_limit and pages >= page_limit:
            log.info(f"  page_limit={page_limit} reached — stopping walk")
            break

    log.info(f"Rankings walk complete: {len(results)} premier-rated players across {pages} pages")
    return results


# ── Snapshot helpers ───────────────────────────────────────────────────────────

def upsert_snapshot(rankings: dict[str, dict]) -> None:
    """Bulk-upsert all fetched rankings into hri_snapshot."""
    if not rankings:
        return
    rows = list(rankings.values())
    # psycopg2 executemany is fine for 18k rows; no need for COPY
    db.executemany(
        """INSERT INTO hri_snapshot (slug, display_name, rating, rd, rank, updated_at)
           VALUES (%s, %s, %s, %s, %s, now())
           ON CONFLICT (slug) DO UPDATE
             SET display_name = EXCLUDED.display_name,
                 rating       = EXCLUDED.rating,
                 rd           = EXCLUDED.rd,
                 rank         = EXCLUDED.rank,
                 updated_at   = now()""",
        [(r["slug"], r["display_name"], r["rating"], r["rd"], r["rank"]) for r in rows],
    )
    log.info(f"hri_snapshot upserted {len(rows)} rows")


def insert_history(rankings: dict[str, dict]) -> None:
    """Append today's snapshot to hri_history (once per day, idempotent)."""
    if not rankings:
        return
    rows = list(rankings.values())
    db.executemany(
        """INSERT INTO hri_history (slug, rating, rd, rank, snapshot_date)
           VALUES (%s, %s, %s, %s, CURRENT_DATE)
           ON CONFLICT (slug, snapshot_date) DO NOTHING""",
        [(r["slug"], r["rating"], r["rd"], r["rank"]) for r in rows],
    )
    log.info(f"hri_history inserted (up to) {len(rows)} rows for today")


# ── Sync ───────────────────────────────────────────────────────────────────────

def sync(force: bool = False, page_limit: int = 0):
    # Load stale player identities that have a melee_username
    stale_clause = "" if force else """
        AND (hri_rating_updated_at IS NULL
             OR hri_rating_updated_at < now() - INTERVAL '6 days')
    """
    players = db.fetchall(f"""
        SELECT id, melee_username
        FROM player_identities
        WHERE melee_username IS NOT NULL AND melee_username != ''
        {stale_clause}
        ORDER BY hri_rating_updated_at ASC NULLS FIRST
    """)
    log.info(f"{len(players)} stale player_identities to refresh")

    if not players:
        log.info("Nothing to do.")
        return

    # Walk the full HRI premier rankings
    rankings = fetch_all_rankings(format="premier", page_limit=page_limit)

    # Persist to snapshot + history tables
    upsert_snapshot(rankings)
    insert_history(rankings)

    # Update player_identities by joining against hri_snapshot
    our_players = {p["melee_username"].lower(): p for p in players}

    updated = 0
    for slug_lower, r in rankings.items():
        p = our_players.get(slug_lower)
        if p is None:
            continue
        db.execute(
            """UPDATE player_identities
               SET hri_rating = %s, hri_rd = %s, hri_rating_updated_at = now()
               WHERE id = %s""",
            (r["rating"], r["rd"], p["id"])
        )
        updated += 1

    # Bump staleness timestamp for stale players not in rankings
    not_found_ids = [
        p["id"] for slug, p in our_players.items()
        if slug not in rankings
    ]
    if not_found_ids:
        db.execute(
            """UPDATE player_identities
               SET hri_rating_updated_at = now()
               WHERE id = ANY(%s::uuid[])""",
            (not_found_ids,)
        )

    log.info(
        f"HRI sync complete — updated: {updated}, "
        f"not in rankings: {len(not_found_ids)} (timestamps bumped)"
    )


# ── Fast new-player lookup (no API) ───────────────────────────────────────────

def lookup_new_players():
    """
    Resolve player_identities that have never had an HRI rating by querying
    hri_snapshot locally — no API walk needed.  Intended to run after new
    players are ingested mid-week.
    """
    new_players = db.fetchall("""
        SELECT id, melee_username
        FROM player_identities
        WHERE melee_username IS NOT NULL AND melee_username != ''
          AND hri_rating_updated_at IS NULL
        ORDER BY id
    """)
    if not new_players:
        log.info("No new players without HRI data.")
        return

    log.info(f"Resolving {len(new_players)} new players against hri_snapshot…")
    found = 0
    for p in new_players:
        snap = db.fetchone(
            """SELECT rating, rd FROM hri_snapshot WHERE lower(slug) = lower(%s)""",
            (p["melee_username"],)
        )
        if snap:
            db.execute(
                """UPDATE player_identities
                   SET hri_rating = %s, hri_rd = %s, hri_rating_updated_at = now()
                   WHERE id = %s""",
                (snap["rating"], snap["rd"], p["id"])
            )
            found += 1
        else:
            # Mark as checked so we don't re-query repeatedly until next weekly walk
            db.execute(
                "UPDATE player_identities SET hri_rating_updated_at = now() WHERE id = %s",
                (p["id"],)
            )

    log.info(f"lookup_new_players complete — found: {found}, not in snapshot: {len(new_players) - found}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Sync HRI ratings into player_identities")
    parser.add_argument("--force",   action="store_true", help="Re-fetch all players, not just stale")
    parser.add_argument("--weekly",  action="store_true", help="Alias for --force (weekly cron job)")
    parser.add_argument("--lookup",  action="store_true", help="Fast-path: resolve new players from hri_snapshot only (no API)")
    parser.add_argument("--limit",   type=int, default=0, help="Max ranking pages to walk (0=all, for testing)")
    args = parser.parse_args()

    if args.lookup:
        lookup_new_players()
    else:
        sync(force=args.force or args.weekly, page_limit=args.limit)
