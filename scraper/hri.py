"""
scraper/hri.py
~~~~~~~~~~~~~~
Syncs HRI (Holocron Rating Index) premier ratings into player_identities
using the HRI public JSON API (https://hri.gg/api).

Strategy:
  Walk /api/v1/rankings?format=premier&limit=200 (cursor-paged, anonymous)
  until the list is exhausted.  The endpoint covers all premier-rated players,
  so no per-player fallback is needed.  Players not found in the rankings walk
  have their hri_rating_updated_at bumped (so we don't retry for 6 days)
  without clearing any rating already on file.

Usage:
    python -m scraper.hri              # update stale players (>6 days old)
    python -m scraper.hri --force      # re-fetch everyone
    python -m scraper.hri --limit 10   # walk at most N pages (for testing)
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
DELAY     = 0.5   # seconds between requests


# ── HTTP ───────────────────────────────────────────────────────────────────────

@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=10))
def _get(path: str, params: dict = None) -> dict:
    r = httpx.get(f"{API_BASE}{path}", params=params, headers=HEADERS, timeout=15)
    r.raise_for_status()
    return r.json()


# ── Rankings walk ──────────────────────────────────────────────────────────────

def fetch_all_rankings(format: str = "premier", page_limit: int = 0) -> dict[str, dict]:
    """
    Walk /api/v1/rankings cursor-paged and return a dict keyed by
    lowercased slug → {slug, rating, rd, rank}.
    `slug` is the melee login handle; `melee_username` on the API is the
    display name and may differ — always key and match by slug.
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
                "slug":   slug,
                "rating": int(round(attrs["rating"])) if attrs.get("rating") is not None else None,
                "rd":     int(round(attrs["rd"]))     if attrs.get("rd")     is not None else None,
                "rank":   rank,
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


# ── Sync ───────────────────────────────────────────────────────────────────────

def sync(force: bool = False, page_limit: int = 0):
    # Load all stale player identities that have a melee_username
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

    # Build index of our stale players by lowercased melee_username
    our_players = {p["melee_username"].lower(): p for p in players}

    # Walk the full HRI premier rankings
    rankings = fetch_all_rankings(format="premier", page_limit=page_limit)

    # Update players found in rankings
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

    # Bump the staleness timestamp for players not in rankings so we skip
    # them for another 6 days (their existing rating is left untouched)
    not_found_ids = [
        p["id"] for slug, p in our_players.items()
        if slug not in rankings
    ]
    if not_found_ids:
        db.execute(
            f"""UPDATE player_identities
                SET hri_rating_updated_at = now()
                WHERE id = ANY(%s::uuid[])""",
            (not_found_ids,)
        )

    log.info(
        f"HRI sync complete — updated: {updated}, "
        f"not in rankings: {len(not_found_ids)} (timestamps bumped)"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Sync HRI ratings into player_identities")
    parser.add_argument("--force", action="store_true", help="Re-fetch all players, not just stale")
    parser.add_argument("--limit", type=int, default=0,  help="Max ranking pages to walk (0=all, for testing)")
    args = parser.parse_args()

    sync(force=args.force, page_limit=args.limit)
