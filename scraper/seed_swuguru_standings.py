"""
scraper/seed_swuguru_standings.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Creates and populates standings, matches, and decklist_cards tables in
the `swuguru` database from Melee tournament data.

Reads the events table for melee_ids, then for each event fetches
standings, matches, and optionally decklists from Melee.

Usage:
    python3 /app/scraper/seed_swuguru_standings.py
    python3 /app/scraper/seed_swuguru_standings.py --decklists   # also fetch card lists
    python3 /app/scraper/seed_swuguru_standings.py --event-id 5  # single event (swuguru id)
    python3 /app/scraper/seed_swuguru_standings.py --limit 3     # first N events
    python3 /app/scraper/seed_swuguru_standings.py --refresh     # re-scrape already-done events
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
from scraper.melee import (
    melee_tournament_rounds,
    melee_round_standings,
    melee_round_matches,
    melee_decklist_cards,
    parse_standing_row,
    _parse_deck_name,
    _parse_record,
    _round_type,
)

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

DECK_DELAY    = 0.4   # seconds between decklist fetches
ROUND_DELAY   = 0.3   # seconds between round fetches

DDL = """
CREATE TABLE IF NOT EXISTS standings (
    id                SERIAL      PRIMARY KEY,
    event_id          INTEGER     NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    melee_player_id   TEXT,
    player_name       TEXT,
    player_slug       TEXT        REFERENCES players(slug),
    placement         INTEGER,
    leader            TEXT,
    base              TEXT,
    melee_deck_id     TEXT,
    decklist_url      TEXT,
    has_decklist      BOOLEAN     NOT NULL DEFAULT FALSE,
    match_wins        INTEGER,
    match_losses      INTEGER,
    match_draws       INTEGER,
    match_win_rate    NUMERIC(7,4),
    game_wins         INTEGER,
    game_losses       INTEGER,
    game_win_rate     NUMERIC(7,4),
    points            INTEGER,
    omwp              NUMERIC(7,4),
    tgwp              NUMERIC(7,4),
    ogwp              NUMERIC(7,4),
    melee_profile_url TEXT,
    scraped_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (event_id, melee_player_id)
);

CREATE INDEX IF NOT EXISTS standings_event_id   ON standings(event_id);
CREATE INDEX IF NOT EXISTS standings_leader      ON standings(leader);
CREATE INDEX IF NOT EXISTS standings_placement   ON standings(placement);
CREATE INDEX IF NOT EXISTS standings_player_slug ON standings(player_slug) WHERE player_slug IS NOT NULL;

CREATE TABLE IF NOT EXISTS matches (
    id              SERIAL      PRIMARY KEY,
    event_id        INTEGER     NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    melee_round_id  INTEGER,
    round_num       INTEGER,
    round_name      TEXT,
    round_type      TEXT,
    p1_standing_id  INTEGER     REFERENCES standings(id),
    p2_standing_id  INTEGER     REFERENCES standings(id),
    p1_leader       TEXT,
    p1_base         TEXT,
    p2_leader       TEXT,
    p2_base         TEXT,
    p1_name         TEXT,
    p1_melee_id     TEXT,
    p1_deck_id      TEXT,
    p1_deck_name    TEXT,
    p1_game_wins    INTEGER,
    p2_name         TEXT,
    p2_melee_id     TEXT,
    p2_deck_id      TEXT,
    p2_deck_name    TEXT,
    p2_game_wins    INTEGER,
    game_draws      INTEGER     NOT NULL DEFAULT 0,
    winner          TEXT,
    result_str      TEXT,
    match_guid      TEXT        UNIQUE,
    phase_id        INTEGER
);

CREATE INDEX IF NOT EXISTS matches_event_id     ON matches(event_id);
CREATE INDEX IF NOT EXISTS matches_p1_leader    ON matches(p1_leader);
CREATE INDEX IF NOT EXISTS matches_p2_leader    ON matches(p2_leader);
CREATE INDEX IF NOT EXISTS matches_round_type   ON matches(round_type);
CREATE INDEX IF NOT EXISTS matches_p1_standing  ON matches(p1_standing_id);
CREATE INDEX IF NOT EXISTS matches_p2_standing  ON matches(p2_standing_id);

CREATE TABLE IF NOT EXISTS decklist_cards (
    id           SERIAL   PRIMARY KEY,
    standing_id  INTEGER  NOT NULL REFERENCES standings(id) ON DELETE CASCADE,
    card_name    TEXT     NOT NULL,
    quantity     INTEGER  NOT NULL DEFAULT 1,
    is_sideboard BOOLEAN  NOT NULL DEFAULT FALSE,
    card_uuid    TEXT     REFERENCES cards(uuid)
);

CREATE INDEX IF NOT EXISTS decklist_cards_standing_id ON decklist_cards(standing_id);
CREATE INDEX IF NOT EXISTS decklist_cards_card_name   ON decklist_cards(card_name);
CREATE INDEX IF NOT EXISTS decklist_cards_card_uuid   ON decklist_cards(card_uuid) WHERE card_uuid IS NOT NULL;
"""


def get_conn():
    return psycopg2.connect(
        host=os.getenv("DB_HOST", "192.168.1.200"),
        port=int(os.getenv("DB_PORT", 5432)),
        dbname="swuguru",
        user=os.getenv("DB_USER", "swu_user"),
        password=os.getenv("DB_PASS", "981273465aA!"),
    )


# ── Upsert helpers ─────────────────────────────────────────────────────────────

def upsert_standing(cur, event_id: int, st: dict) -> int:
    melee_player_id = st.get("melee_player_id") or st.get("melee_deck_id") or str(st.get("place", ""))
    mw, ml, md = _parse_record(st.get("swiss_record", ""))
    gw, gl, _  = _parse_record(st.get("game_record", ""))
    mwr = round(mw / (mw + ml + md), 4) if (mw + ml + md) > 0 else None
    gwr = round(gw / (gw + gl),      4) if (gw + gl) > 0 else None

    deck_id  = st.get("melee_deck_id") or ""
    username = st.get("melee_username") or ""

    cur.execute(
        """
        INSERT INTO standings
            (event_id, melee_player_id, player_name, placement,
             leader, base, melee_deck_id, decklist_url, has_decklist,
             match_wins, match_losses, match_draws, match_win_rate,
             game_wins, game_losses, game_win_rate,
             points, omwp, tgwp, ogwp, melee_profile_url, scraped_at)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,now())
        ON CONFLICT (event_id, melee_player_id) DO UPDATE SET
            player_name       = EXCLUDED.player_name,
            placement         = COALESCE(EXCLUDED.placement,   standings.placement),
            leader            = COALESCE(EXCLUDED.leader,      standings.leader),
            base              = COALESCE(EXCLUDED.base,        standings.base),
            melee_deck_id     = COALESCE(NULLIF(EXCLUDED.melee_deck_id,''), standings.melee_deck_id),
            decklist_url      = COALESCE(EXCLUDED.decklist_url,  standings.decklist_url),
            has_decklist      = EXCLUDED.has_decklist OR standings.has_decklist,
            match_wins        = COALESCE(EXCLUDED.match_wins,   standings.match_wins),
            match_losses      = COALESCE(EXCLUDED.match_losses, standings.match_losses),
            match_draws       = COALESCE(EXCLUDED.match_draws,  standings.match_draws),
            match_win_rate    = COALESCE(EXCLUDED.match_win_rate, standings.match_win_rate),
            game_wins         = COALESCE(EXCLUDED.game_wins,    standings.game_wins),
            game_losses       = COALESCE(EXCLUDED.game_losses,  standings.game_losses),
            game_win_rate     = COALESCE(EXCLUDED.game_win_rate, standings.game_win_rate),
            points            = COALESCE(EXCLUDED.points,       standings.points),
            omwp              = COALESCE(EXCLUDED.omwp,         standings.omwp),
            tgwp              = COALESCE(EXCLUDED.tgwp,         standings.tgwp),
            ogwp              = COALESCE(EXCLUDED.ogwp,         standings.ogwp),
            melee_profile_url = COALESCE(EXCLUDED.melee_profile_url, standings.melee_profile_url),
            scraped_at        = now()
        RETURNING id
        """,
        (
            event_id,
            melee_player_id,
            st.get("player_name"),
            st.get("place"),
            st.get("leader"),
            st.get("base"),
            deck_id or None,
            st.get("melee_deck_url") or None,
            bool(deck_id),
            mw or None, ml or None, md or None, mwr,
            gw or None, gl or None, gwr,
            st.get("swiss_points"),
            st.get("omw_pct"),
            st.get("tgw_pct"),
            st.get("ogw_pct"),
            f"https://melee.gg/Profile/Index/{username}" if username else None,
        ),
    )
    return cur.fetchone()[0]


def upsert_match(cur, event_id: int, round_id: int, round_num: int,
                 round_name: str, match: dict, standing_map: dict):
    p1_sid = standing_map.get(match.get("p1_melee_id"))
    p2_sid = standing_map.get(match.get("p2_melee_id"))

    p1l, p1b = _parse_deck_name(match.get("p1_deck_name", ""))
    p2l, p2b = _parse_deck_name(match.get("p2_deck_name", ""))

    cur.execute(
        """
        INSERT INTO matches
            (event_id, melee_round_id, round_num, round_name, round_type,
             p1_standing_id, p2_standing_id,
             p1_leader, p1_base, p2_leader, p2_base,
             p1_name, p1_melee_id, p1_deck_id, p1_deck_name, p1_game_wins,
             p2_name, p2_melee_id, p2_deck_id, p2_deck_name, p2_game_wins,
             game_draws, winner, result_str, match_guid, phase_id)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT (match_guid) DO UPDATE SET
            p1_leader    = COALESCE(EXCLUDED.p1_leader,    matches.p1_leader),
            p1_base      = COALESCE(EXCLUDED.p1_base,      matches.p1_base),
            p2_leader    = COALESCE(EXCLUDED.p2_leader,    matches.p2_leader),
            p2_base      = COALESCE(EXCLUDED.p2_base,      matches.p2_base),
            p1_deck_name = COALESCE(NULLIF(EXCLUDED.p1_deck_name,''), matches.p1_deck_name),
            p2_deck_name = COALESCE(NULLIF(EXCLUDED.p2_deck_name,''), matches.p2_deck_name),
            p1_standing_id = COALESCE(EXCLUDED.p1_standing_id, matches.p1_standing_id),
            p2_standing_id = COALESCE(EXCLUDED.p2_standing_id, matches.p2_standing_id)
        """,
        (
            event_id, round_id, round_num, round_name, _round_type(round_name),
            p1_sid, p2_sid,
            p1l, p1b, p2l, p2b,
            match["p1_name"], match["p1_melee_id"], match["p1_deck_id"],
            match["p1_deck_name"], match["p1_game_wins"],
            match["p2_name"], match["p2_melee_id"], match["p2_deck_id"],
            match["p2_deck_name"], match["p2_game_wins"],
            match.get("game_draws", 0), match["winner"],
            match.get("result_str"), match.get("match_guid"), match.get("phase_id"),
        ),
    )


def save_decklist(cur, standing_id: int, cards: list[dict], card_uuid_map: dict):
    cur.execute("DELETE FROM decklist_cards WHERE standing_id = %s", (standing_id,))
    rows = [
        (standing_id, c["card_name"], c["quantity"], c["is_sideboard"],
         card_uuid_map.get(c["card_name"]))
        for c in cards
    ]
    if rows:
        psycopg2.extras.execute_values(
            cur,
            "INSERT INTO decklist_cards (standing_id, card_name, quantity, is_sideboard, card_uuid) VALUES %s",
            rows,
        )


# ── Per-event import ──────────────────────────────────────────────────────────

def _elim_priority(r: dict) -> int:
    order = {"final": 3, "semifinal": 2, "quarterfinal": 1, "top ": 0}
    return max((v for k, v in order.items() if r["name"].lower().startswith(k)), default=-1)


def import_event(conn, event: dict, fetch_decklists: bool = False, card_uuid_map: dict = None):
    melee_id  = event["melee_id"]
    event_id  = event["id"]
    log.info(f"  Fetching rounds for melee_id={melee_id} …")

    page = melee_tournament_rounds(melee_id)
    standings_rounds = page["standings_rounds"]
    pairings_rounds  = page["pairings_rounds"]

    if not standings_rounds:
        log.warning("    No rounds found, skipping")
        return False

    # Pick best round for standings: completed elim first (finals > semis > quarters),
    # then last completed Swiss, then last overall.
    completed = [r for r in standings_rounds if r.get("completed")]
    elim  = sorted([r for r in completed if _elim_priority(r) >= 0], key=_elim_priority, reverse=True)
    swiss = [r for r in completed if _elim_priority(r) < 0]
    candidates = elim + list(reversed(swiss)) or list(reversed(standings_rounds))

    raw_standings = []
    standings_round = None
    for candidate in candidates:
        raw_standings = melee_round_standings(candidate["id"])
        if raw_standings:
            standings_round = candidate
            break

    if not raw_standings:
        log.warning("    No standings rows found in any round")
        return False

    log.info(f"    {len(standings_rounds)} rounds — standings from '{standings_round['name']}'")

    # ── Standings ──
    cur = conn.cursor()
    standing_map = {}   # melee_player_id → standing row id
    for raw in raw_standings:
        st   = parse_standing_row(raw)
        sid  = upsert_standing(cur, event_id, st)
        if st.get("melee_player_id"):
            standing_map[st["melee_player_id"]] = sid

    conn.commit()
    log.info(f"    {len(raw_standings)} standings upserted")

    # ── Matches — walk all pairings rounds ──
    total_matches = 0
    for i, rnd in enumerate(pairings_rounds):
        matches = melee_round_matches(rnd["id"])
        for m in matches:
            upsert_match(cur, event_id, rnd["id"], i + 1, rnd["name"], m, standing_map)
        total_matches += len(matches)
        time.sleep(ROUND_DELAY)

    conn.commit()
    log.info(f"    {total_matches} matches upserted across {len(pairings_rounds)} rounds")

    # ── Decklists ──
    if fetch_decklists:
        cur.execute(
            "SELECT id, melee_deck_id FROM standings WHERE event_id=%s AND has_decklist=TRUE AND melee_deck_id IS NOT NULL",
            (event_id,)
        )
        deck_rows = cur.fetchall()
        fetched = 0
        for sid, deck_id in deck_rows:
            cards = melee_decklist_cards(deck_id)
            if cards:
                save_decklist(cur, sid, cards, card_uuid_map or {})
                fetched += 1
            time.sleep(DECK_DELAY)
        conn.commit()
        log.info(f"    {fetched}/{len(deck_rows)} decklists fetched")

    cur.close()
    return True


# ── Main ──────────────────────────────────────────────────────────────────────

def main(event_id: int = 0, limit: int = 0, fetch_decklists: bool = False, refresh: bool = False):
    conn = get_conn()
    cur  = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    log.info("Creating tables …")
    cur.execute(DDL)
    conn.commit()

    # Build card name → uuid lookup for decklist resolution
    card_uuid_map = {}
    if fetch_decklists:
        cur.execute("SELECT name, uuid FROM cards WHERE variant_type = 'Standard' OR variant_type IS NULL")
        for row in cur.fetchall():
            card_uuid_map[row["name"]] = row["uuid"]
        log.info(f"  Loaded {len(card_uuid_map)} card name→uuid mappings")

    # Load events to process
    where_clauses = ["melee_id IS NOT NULL"]
    params = []

    if event_id:
        where_clauses.append("id = %s")
        params.append(event_id)
    elif not refresh:
        # Skip events that already have standings
        where_clauses.append("""id NOT IN (SELECT DISTINCT event_id FROM standings)""")

    where = " AND ".join(where_clauses)
    cur.execute(f"SELECT id, melee_id, name, date FROM events WHERE {where} ORDER BY date DESC", params)
    events = cur.fetchall()

    if limit:
        events = events[:limit]

    log.info(f"Processing {len(events)} events …")
    cur.close()

    ok = skipped = 0
    for i, ev in enumerate(events, 1):
        log.info(f"[{i}/{len(events)}] {ev['name']} ({ev['date']}) melee_id={ev['melee_id']}")
        try:
            success = import_event(conn, ev, fetch_decklists=fetch_decklists, card_uuid_map=card_uuid_map)
            if success:
                ok += 1
            else:
                skipped += 1
        except Exception as e:
            log.error(f"  Failed: {e}")
            conn.rollback()
            skipped += 1

    # Summary
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM standings")
    n_standings = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM matches")
    n_matches = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM decklist_cards")
    n_cards = cur.fetchone()[0]
    cur.close()
    conn.close()

    log.info(
        f"\nDone — {ok} events imported, {skipped} skipped\n"
        f"  standings: {n_standings}\n"
        f"  matches:   {n_matches}\n"
        f"  decklist_cards: {n_cards}"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Seed swuguru standings/matches/decklists from Melee")
    parser.add_argument("--event-id", type=int, default=0,
                        help="Process a single event by swuguru events.id")
    parser.add_argument("--limit", type=int, default=0,
                        help="Max number of events to process (0=all)")
    parser.add_argument("--decklists", action="store_true",
                        help="Also fetch decklist cards for each player")
    parser.add_argument("--refresh", action="store_true",
                        help="Re-scrape events that already have standings")
    args = parser.parse_args()
    main(event_id=args.event_id, limit=args.limit, fetch_decklists=args.decklists, refresh=args.refresh)
