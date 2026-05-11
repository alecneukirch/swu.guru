"""
scraper/hri.py
~~~~~~~~~~~~~~
Syncs HRI (Holocron Rating Index) Glicko-2 ratings for all known players
from hri.gg into player_identities.hri_rating / hri_rd / hri_rating_updated_at.

Usage:
    python -m scraper.hri              # update stale players (>6 days old)
    python -m scraper.hri --force      # re-fetch everyone
    python -m scraper.hri --limit 50   # test run on first 50 players
"""

import argparse
import logging
import re
import time

import httpx
from bs4 import BeautifulSoup
from tenacity import retry, stop_after_attempt, wait_exponential

import db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)

HRI_BASE = "https://hri.gg"
HEADERS  = {"User-Agent": "SWUGuru/1.0 (analytics; contact alecneukirch@gmail.com)"}
DELAY    = 0.5   # seconds between requests


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=10))
def _fetch_player(username: str) -> httpx.Response:
    url = f"{HRI_BASE}/players/{username}?fmt=premier"
    r = httpx.get(url, headers=HEADERS, timeout=15, follow_redirects=True)
    if r.status_code != 404:
        r.raise_for_status()
    return r


def _parse_rating(soup: BeautifulSoup) -> tuple[int | None, int | None]:
    """Return premier (rating, rd) from a player profile page, or (None, None)."""
    for stat in soup.select(".profile-hero__stats .hero-stat"):
        label = stat.select_one(".hero-stat__label")
        if label and label.get_text(strip=True) == "Premier":
            value = stat.select_one(".hero-stat__value")
            note  = stat.select_one(".hero-stat__note")
            if not value:
                return None, None
            try:
                rating = int(value.get_text(strip=True).replace(",", ""))
            except ValueError:
                return None, None
            rd = None
            if note:
                rd_str = re.sub(r"[^\d]", "", note.get_text(strip=True))
                try:
                    rd = int(rd_str)
                except ValueError:
                    pass
            return rating, rd
    return None, None


def sync(force: bool = False, limit: int | None = None):
    stale_clause = "" if force else """
        AND (hri_rating_updated_at IS NULL
             OR hri_rating_updated_at < now() - INTERVAL '6 days')
    """
    limit_clause = f"LIMIT {int(limit)}" if limit else ""

    players = db.fetchall(f"""
        SELECT id, melee_username
        FROM player_identities
        WHERE melee_username IS NOT NULL AND melee_username != ''
        {stale_clause}
        ORDER BY hri_rating_updated_at ASC NULLS FIRST
        {limit_clause}
    """)

    log.info(f"Fetching HRI ratings for {len(players)} players…")
    updated = skipped = errors = 0

    for i, p in enumerate(players):
        username = p["melee_username"]
        try:
            resp = _fetch_player(username)

            if resp.status_code == 404:
                # Player not on HRI — mark checked so we don't retry for 6 days
                db.execute(
                    "UPDATE player_identities SET hri_rating_updated_at = now() WHERE id = %s",
                    (p["id"],)
                )
                skipped += 1
                log.debug(f"  {username}: not on HRI (404)")
            else:
                soup = BeautifulSoup(resp.text, "lxml")
                rating, rd = _parse_rating(soup)
                db.execute(
                    """UPDATE player_identities
                       SET hri_rating = %s, hri_rd = %s, hri_rating_updated_at = now()
                       WHERE id = %s""",
                    (rating, rd, p["id"])
                )
                updated += 1
                log.info(f"  [{i+1}/{len(players)}] {username}: {rating} ±{rd}")

        except Exception as e:
            log.warning(f"  {username}: error — {e}")
            errors += 1

        time.sleep(DELAY)

    log.info(
        f"HRI sync complete — updated: {updated}, not on HRI: {skipped}, errors: {errors}"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Sync HRI ratings into player_identities")
    parser.add_argument("--force", action="store_true", help="Re-fetch all players, not just stale")
    parser.add_argument("--limit", type=int, default=None, help="Only process N players (for testing)")
    args = parser.parse_args()

    sync(force=args.force, limit=args.limit)
