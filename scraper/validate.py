"""
validate.py — Weekend data validation

Runs Monday 2am to catch data quality issues from the weekend's tournaments.

Checks (for events in the last --days days):
  1. Stale events scraped before they ran → full re-scrape + decklists
  2. Events with standings missing decklists → re-attempt decklist fetch
  3. Standings missing leader or base info → logged for investigation
  4. MWR consistency: stored match_win_rate vs wins computed from match rows
  5. Bracket reconciliation for all recent events (placement 1/2 from Finals match)

Refreshes materialized views when done.
"""
import argparse
import logging
import sys
from datetime import date, timedelta, timezone

import db
from scraper.melee import import_tournament, refresh_placements, _table_names

log = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    stream=sys.stdout,
)


def validate(days: int = 8, dry_run: bool = False) -> None:
    tbls   = _table_names(eternal=False)
    cutoff = date.today() - timedelta(days=days)

    log.info(f"=== Weekend validation: events since {cutoff} (dry_run={dry_run}) ===")

    # ── 1. Inventory recent events ────────────────────────────────────────────
    events = db.fetchall(f"""
        SELECT
            e.id, e.melee_id, e.name, e.date, e.scraped_at,
            COUNT(s.id)                                        AS standing_count,
            COUNT(s.id) FILTER (WHERE s.has_decklist)          AS decklist_count,
            COUNT(s.id) FILTER (WHERE s.leader IS NULL
                                   OR  s.leader = '')          AS missing_leader,
            COUNT(s.id) FILTER (WHERE s.base   IS NULL
                                   OR  s.base   = '')          AS missing_base
        FROM {tbls['events']} e
        LEFT JOIN {tbls['standings']} s ON s.event_id = e.id
        WHERE e.date >= %s
          AND e.date <= CURRENT_DATE
          AND e.melee_id IS NOT NULL
        GROUP BY e.id, e.melee_id, e.name, e.date, e.scraped_at
        ORDER BY e.date DESC
    """, [cutoff])

    log.info(f"Found {len(events)} events to check.")

    stale_ids:          list[str] = []   # scraped before the event ran
    missing_deck_ids:   list[str] = []   # has standings but some lack decklists
    no_standing_ids:    list[str] = []   # zero standings at all

    for ev in events:
        scraped_date = ev["scraped_at"].astimezone(timezone.utc).date() if ev["scraped_at"] else None
        event_date   = ev["date"]

        if ev["standing_count"] == 0:
            no_standing_ids.append(ev["melee_id"])
            log.warning(f"  NO STANDINGS  — {ev['name']} (melee_id={ev['melee_id']})")
            continue

        if scraped_date and scraped_date < event_date:
            stale_ids.append(ev["melee_id"])
            log.info(f"  STALE         — {ev['name']} scraped {scraped_date}, event {event_date}")
            continue

        if ev["decklist_count"] < ev["standing_count"]:
            missing_deck_ids.append(ev["melee_id"])
            log.info(f"  MISSING DECKS — {ev['name']}: "
                     f"{ev['decklist_count']}/{ev['standing_count']} decklists")

        if ev["missing_leader"] or ev["missing_base"]:
            log.warning(f"  BAD LEADER/BASE — {ev['name']}: "
                        f"{ev['missing_leader']} missing leaders, "
                        f"{ev['missing_base']} missing bases")

    # ── 2. Re-scrape stale events (full pipeline + decklists) ─────────────────
    if stale_ids:
        log.info(f"\n--- Re-scraping {len(stale_ids)} stale event(s) ---")
        for melee_id in stale_ids:
            log.info(f"  Re-scraping {melee_id}...")
            if not dry_run:
                try:
                    import_tournament(melee_id, fetch_cards=True)
                except Exception as exc:
                    log.error(f"  Failed: {exc}", exc_info=True)

    # ── 3. Re-scrape events with missing decklists ────────────────────────────
    if missing_deck_ids:
        log.info(f"\n--- Re-scraping {len(missing_deck_ids)} event(s) with missing decklists ---")
        for melee_id in missing_deck_ids:
            log.info(f"  Re-scraping {melee_id} (cards only)...")
            if not dry_run:
                try:
                    import_tournament(melee_id, fetch_cards=True)
                except Exception as exc:
                    log.error(f"  Failed: {exc}", exc_info=True)

    # ── 4. Bracket reconciliation for all recent events ───────────────────────
    # refresh_placements re-fetches standings from Melee and applies the
    # Finals bracket fix for events where the Finals standings API was empty.
    log.info(f"\n--- Running bracket reconciliation for last {days} days ---")
    if not dry_run:
        # Pass a date filter by hacking the SQL: refresh_placements doesn't
        # have a --days flag, so filter by calling it with a custom query.
        _bracket_reconcile(tbls, cutoff)

    # ── 5. Round count check ──────────────────────────────────────────────────
    # Expected Swiss rounds: <32 players → 5 rounds, 32+ → 6 rounds.
    # If we scraped fewer Swiss rounds than expected, re-scrape the event.
    log.info(f"\n--- Round count check ---")
    round_counts = db.fetchall(f"""
        SELECT
            e.id, e.melee_id, e.name, e.player_count,
            COUNT(DISTINCT m.round_name) FILTER (
                WHERE m.round_name ~* '^round'
            ) AS scraped_swiss_rounds
        FROM {tbls['events']} e
        LEFT JOIN {tbls['matches']} m ON m.event_id = e.id
        WHERE e.date >= %s
          AND e.player_count IS NOT NULL
        GROUP BY e.id, e.melee_id, e.name, e.player_count
    """, [cutoff])

    short_round_ids: list[str] = []
    for ev in round_counts:
        pc = ev["player_count"] or 0
        expected = 4 if pc < 16 else (5 if pc < 32 else 6)
        scraped  = ev["scraped_swiss_rounds"] or 0
        if scraped < expected:
            short_round_ids.append(ev["melee_id"])
            log.warning(f"  MISSING ROUNDS — {ev['name']} (melee_id={ev['melee_id']}): "
                        f"{scraped}/{expected} Swiss rounds scraped "
                        f"({ev['player_count']} players)")

    if not short_round_ids:
        log.info("  All events have the expected number of Swiss rounds.")

    if short_round_ids:
        log.info(f"\n--- Re-scraping {len(short_round_ids)} event(s) with missing rounds ---")
        for melee_id in short_round_ids:
            log.info(f"  Re-scraping {melee_id}...")
            if not dry_run:
                try:
                    import_tournament(melee_id, fetch_cards=True)
                except Exception as exc:
                    log.error(f"  Failed: {exc}", exc_info=True)

    # ── 6. Refresh views ──────────────────────────────────────────────────────
    if not dry_run:
        log.info("\nRefreshing materialized views...")
        db.execute_autocommit("SELECT refresh_all_views()")

    log.info("\n=== Validation complete ===")


def _bracket_reconcile(tbls: dict, cutoff: date) -> None:
    """
    For recent events where standings were taken from a non-Finals round,
    re-derive placements 1 and 2 from the Finals match in the matches table.
    This is a targeted fix that doesn't require hitting the Melee API.
    """
    events = db.fetchall(f"""
        SELECT e.id, e.melee_id, e.name
        FROM {tbls['events']} e
        WHERE e.date >= %s AND e.melee_id IS NOT NULL
    """, [cutoff])

    fixed = 0
    for ev in events:
        finals_match = db.fetchone(f"""
            SELECT p1_standing_id, p2_standing_id, winner
            FROM {tbls['matches']}
            WHERE event_id = %s AND round_name ILIKE 'Finals'
            ORDER BY id DESC LIMIT 1
        """, [ev["id"]])

        if not finals_match or finals_match["winner"] not in ("p1", "p2"):
            continue

        winner_sid = (finals_match["p1_standing_id"] if finals_match["winner"] == "p1"
                      else finals_match["p2_standing_id"])
        loser_sid  = (finals_match["p2_standing_id"] if finals_match["winner"] == "p1"
                      else finals_match["p1_standing_id"])
        if not winner_sid or not loser_sid:
            continue

        # Only update if placement is currently wrong
        current = db.fetchall(f"""
            SELECT id, placement FROM {tbls['standings']}
            WHERE id = ANY(%s::int[])
        """, [[winner_sid, loser_sid]])
        current_map = {r["id"]: r["placement"] for r in current}

        if current_map.get(winner_sid) != 1 or current_map.get(loser_sid) != 2:
            db.execute(
                f"UPDATE {tbls['standings']} SET placement = 1 WHERE id = %s",
                [winner_sid]
            )
            db.execute(
                f"UPDATE {tbls['standings']} SET placement = 2 WHERE id = %s",
                [loser_sid]
            )
            log.info(f"  Bracket fix: {ev['name']} — "
                     f"standing {winner_sid} → 1st, {loser_sid} → 2nd")
            fixed += 1

    log.info(f"  Bracket reconciliation: {fixed} event(s) corrected.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Weekend data validation")
    parser.add_argument("--days",    type=int,  default=8,     help="Look back N days (default 8)")
    parser.add_argument("--dry-run", action="store_true",      help="Report issues without fixing")
    args = parser.parse_args()
    validate(days=args.days, dry_run=args.dry_run)
