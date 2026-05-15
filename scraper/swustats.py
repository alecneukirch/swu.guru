"""
scraper/swustats.py
~~~~~~~~~~~~~~~~~~~
Syncs weekly meta statistics from swustats.net:
  - MetaMatchupStatsAPI  →  swustats_matchup_stats
  - CardMetaStatsAPI     →  swustats_card_stats  (one call per unique deck archetype)

Usage:
    python -m scraper.swustats --week 52          # sync both tables for week 52
    python -m scraper.swustats --week 52 --matchups  # matchup stats only
    python -m scraper.swustats --week 52 --cards     # card stats only
    python -m scraper.swustats --week 52 --force     # ignore last-sync timestamp
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


# ── HTTP ───────────────────────────────────────────────────────────────────

@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=10))
def get(path: str, params: dict = None) -> list | dict:
    r = httpx.get(BASE_URL + path, params=params, headers=HEADERS, timeout=30)
    r.raise_for_status()
    return r.json()


def _pct(value: str | None) -> Decimal | None:
    """Convert a percentage string like '56.3%' or '56.3' to a Decimal."""
    if value is None:
        return None
    try:
        return Decimal(str(value).rstrip("%"))
    except InvalidOperation:
        return None


# ── Sync: matchup stats ────────────────────────────────────────────────────

def sync_matchup_stats(week_num: int, force: bool = False) -> int:
    resource = f"swustats_matchup_week_{week_num}"
    if not force and already_synced(resource):
        log.info(f"Matchup stats week {week_num} already synced — skipping (use --force)")
        return 0

    log.info(f"Fetching matchup stats for week {week_num}…")
    rows = get("/APIs/MetaMatchupStatsAPI.php", {"week": week_num})
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

    mark_synced(resource, upserted)
    log.info(f"Matchup stats sync complete: {upserted} upserted")
    return upserted


# ── Sync: card stats ───────────────────────────────────────────────────────

def sync_card_stats(week_num: int, force: bool = False) -> int:
    resource = f"swustats_cards_week_{week_num}"
    if not force and already_synced(resource):
        log.info(f"Card stats week {week_num} already synced — skipping (use --force)")
        return 0

    # Pull unique deck archetypes from the matchup table for this week
    decks = db.fetchall(
        """
        SELECT DISTINCT leader_id, base_id
        FROM swustats_matchup_stats
        WHERE week_num = %s
        """,
        (week_num,)
    )
    if not decks:
        log.warning(f"No matchup rows found for week {week_num} — run --matchups first")
        return 0

    log.info(f"Fetching card stats for {len(decks)} deck archetypes (week {week_num})…")

    total = 0
    for i, deck in enumerate(decks, 1):
        leader_id = deck["leader_id"]
        base_id   = deck["base_id"]

        cards = get(
            "/Stats/CardMetaStatsAPI.php",
            {"leaderID": leader_id, "baseID": base_id, "week": week_num},
        )
        if not isinstance(cards, list):
            log.warning(f"  Unexpected response for {leader_id}/{base_id}: {type(cards)}")
            continue

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
                    %s, %s, %s,
                    %s, %s,
                    %s, %s, %s,
                    %s, %s, %s,
                    %s, %s, %s,
                    NOW()
                )
                ON CONFLICT (week_num, leader_id, base_id, card_uid)
                DO UPDATE SET
                    card_name                 = EXCLUDED.card_name,
                    times_included            = EXCLUDED.times_included,
                    times_included_in_wins    = EXCLUDED.times_included_in_wins,
                    percent_included_in_wins  = EXCLUDED.percent_included_in_wins,
                    times_played              = EXCLUDED.times_played,
                    times_played_in_wins      = EXCLUDED.times_played_in_wins,
                    percent_played_in_wins    = EXCLUDED.percent_played_in_wins,
                    times_resourced           = EXCLUDED.times_resourced,
                    times_resourced_in_wins   = EXCLUDED.times_resourced_in_wins,
                    percent_resourced_in_wins = EXCLUDED.percent_resourced_in_wins,
                    synced_at                 = NOW()
                """,
                (
                    week_num, leader_id, base_id,
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

        log.info(f"  [{i}/{len(decks)}] {leader_id}/{base_id}: {len(cards)} cards")
        time.sleep(0.1)  # be polite

    mark_synced(resource, total)
    log.info(f"Card stats sync complete: {total} rows upserted")
    return total


# ── Sync state helpers ─────────────────────────────────────────────────────

def already_synced(resource: str) -> bool:
    try:
        row = db.fetchone(
            "SELECT synced_at FROM sync_state WHERE resource = %s",
            (resource,)
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

def run(week_num: int, matchups: bool = True, cards: bool = True, force: bool = False):
    if matchups:
        sync_matchup_stats(week_num, force)
    if cards:
        sync_card_stats(week_num, force)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Sync swustats.net weekly meta data")
    parser.add_argument("--week",     type=int, required=True, help="Week number to sync")
    parser.add_argument("--matchups", action="store_true", help="Sync matchup stats only")
    parser.add_argument("--cards",    action="store_true", help="Sync card stats only")
    parser.add_argument("--force",    action="store_true", help="Re-sync even if already done")
    args = parser.parse_args()

    sync_all = not (args.matchups or args.cards)

    run(
        week_num = args.week,
        matchups = sync_all or args.matchups,
        cards    = sync_all or args.cards,
        force    = args.force,
    )
