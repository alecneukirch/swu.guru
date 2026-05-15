"""
scraper/swustats.py
~~~~~~~~~~~~~~~~~~~
Syncs weekly meta statistics from swustats.net:
  - MetaMatchupStatsAPI  →  swustats_matchup_stats  (run weekly)
  - CardMetaStatsAPI     →  swustats_card_stats      (run once to map card IDs to leaders/bases)

week_num=0 is the rolling "current week" slot — always re-fetched, no week
param sent to the API so the server returns whatever it considers current.
Historical weeks (week_num > 0) are written once and never overwritten.

Usage:
    python -m scraper.swustats                    # current week matchups only
    python -m scraper.swustats --week 52          # historical week matchups only
    python -m scraper.swustats --cards            # one-time card → leader/base mapping
"""

import argparse
import logging
import time
from decimal import Decimal, InvalidOperation

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

import db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)

BASE_URL = "https://swustats.net/TCGEngine"
HEADERS  = {"User-Agent": "SWUCards/1.0 (personal analytics tool)"}
CURRENT  = 0  # sentinel stored in week_num for "current week"


# ── HTTP ───────────────────────────────────────────────────────────────────

@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=10))
def get(path: str, params: dict = None) -> list | dict:
    r = httpx.get(BASE_URL + path, params=params, headers=HEADERS, timeout=30)
    r.raise_for_status()
    return r.json()


def _pct(value: str | None) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value).rstrip("%"))
    except InvalidOperation:
        return None


# ── Sync: matchup stats (weekly) ───────────────────────────────────────────

def sync_matchup_stats(week_num: int, force: bool = False) -> int:
    label = "current" if week_num == CURRENT else f"week {week_num}"

    if week_num != CURRENT and not force and already_synced(f"swustats_matchup_{week_num}"):
        log.info(f"Matchup stats {label} already synced — skipping (use --force)")
        return 0

    params = {} if week_num == CURRENT else {"week": week_num}
    log.info(f"Fetching matchup stats ({label})…")
    rows = get("/APIs/MetaMatchupStatsAPI.php", params)
    log.info(f"  {len(rows)} matchup rows")

    upserted = 0
    for r in rows:
        db.execute(
            """
            INSERT INTO swustats_matchup_stats (
                week_num, leader_id, base_id,
                opponent_leader_id, opponent_base_id,
                num_wins, num_plays, plays_going_first,
                turns_in_wins, total_turns,
                cards_resourced_in_wins, total_cards_resourced,
                remaining_health_in_wins,
                wins_going_first, wins_going_second,
                synced_at
            ) VALUES (
                %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s,
                NOW()
            )
            ON CONFLICT (week_num, leader_id, base_id, opponent_leader_id, opponent_base_id)
            DO UPDATE SET
                num_wins                 = EXCLUDED.num_wins,
                num_plays                = EXCLUDED.num_plays,
                plays_going_first        = EXCLUDED.plays_going_first,
                turns_in_wins            = EXCLUDED.turns_in_wins,
                total_turns              = EXCLUDED.total_turns,
                cards_resourced_in_wins  = EXCLUDED.cards_resourced_in_wins,
                total_cards_resourced    = EXCLUDED.total_cards_resourced,
                remaining_health_in_wins = EXCLUDED.remaining_health_in_wins,
                wins_going_first         = EXCLUDED.wins_going_first,
                wins_going_second        = EXCLUDED.wins_going_second,
                synced_at                = NOW()
            WHERE swustats_matchup_stats.week_num = 0
            """,
            (
                week_num,
                r["leaderID"], r["baseID"],
                r["opponentLeaderID"], r["opponentBaseID"],
                r.get("numWins", 0), r.get("numPlays", 0), r.get("playsGoingFirst", 0),
                r.get("turnsInWins", 0), r.get("totalTurns", 0),
                r.get("cardsResourcedInWins", 0), r.get("totalCardsResourced", 0),
                r.get("remainingHealthInWins", 0),
                r.get("winsGoingFirst", 0), r.get("winsGoingSecond", 0),
            )
        )
        upserted += 1

    if week_num != CURRENT:
        mark_synced(f"swustats_matchup_{week_num}", upserted)
    log.info(f"Matchup stats sync complete: {upserted} rows")
    return upserted


# ── Sync: card stats (one-time) ────────────────────────────────────────────

def sync_card_stats(force: bool = False) -> int:
    """Fetch the full card list once from CardMetaStatsAPI to map card IDs/names.
    Uses DO NOTHING — existing rows are never overwritten."""

    if not force and already_synced("swustats_cards"):
        log.info("Card stats already synced — skipping (use --force)")
        return 0

    log.info("Fetching card list from CardMetaStatsAPI…")
    cards = get("/Stats/CardMetaStatsAPI.php")
    if not isinstance(cards, list):
        log.error(f"Unexpected response: {type(cards)}")
        return 0
    log.info(f"  {len(cards)} cards")

    total = 0
    for c in cards:
        db.execute(
            """
            INSERT INTO swustats_card_stats (
                week_num, leader_id, base_id,
                card_uid, card_name,
                times_included, times_included_in_wins, percent_included_in_wins,
                times_played, times_played_in_wins, percent_played_in_wins,
                times_resourced, times_resourced_in_wins, percent_resourced_in_wins,
                synced_at
            ) VALUES (
                0, '', '',
                %s, %s,
                %s, %s, %s, %s, %s,
                %s, %s, %s, %s,
                NOW()
            )
            ON CONFLICT (week_num, leader_id, base_id, card_uid) DO NOTHING
            """,
            (
                c["cardUid"], c.get("cardName"),
                c.get("timesIncluded", 0),
                c.get("timesIncludedInWins", 0),
                _pct(c.get("percentIncludedInWins")),
                c.get("timesPlayed", 0),
                c.get("timesPlayedInWins", 0),
                _pct(c.get("percentPlayedInWins")),
                c.get("timesResourced", 0),
                c.get("timesResourcedInWins", 0),
                _pct(c.get("percentResourcedInWins")),
            )
        )
        total += 1

    mark_synced("swustats_cards", total)
    log.info(f"Card stats sync complete: {total} rows")
    return total


# ── Sync state helpers ─────────────────────────────────────────────────────

def already_synced(resource: str) -> bool:
    try:
        row = db.fetchone(
            "SELECT synced_at FROM sync_state WHERE resource = %s", (resource,)
        )
        return row is not None
    except Exception:
        return False


def mark_synced(resource: str, count: int):
    try:
        db.execute(
            """
            INSERT INTO sync_state (resource, synced_at, record_count)
            VALUES (%s, NOW(), %s)
            ON CONFLICT (resource) DO UPDATE
               SET synced_at    = NOW(),
                   record_count = EXCLUDED.record_count
            """,
            (resource, count)
        )
    except Exception as e:
        log.warning(f"Could not update sync_state for {resource}: {e}")


# ── Entry point ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Sync swustats.net meta data")
    parser.add_argument("--week",  type=int, default=None,
                        help="Week number to sync (omit for current week)")
    parser.add_argument("--cards", action="store_true",
                        help="One-time sync of card → leader/base mapping")
    parser.add_argument("--force", action="store_true",
                        help="Re-sync even if already done")
    args = parser.parse_args()

    if args.cards:
        sync_card_stats(force=args.force)
    else:
        week_num = args.week if args.week is not None else CURRENT
        sync_matchup_stats(week_num, force=args.force)
