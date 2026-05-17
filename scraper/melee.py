"""
scraper/melee.py
~~~~~~~~~~~~~~~~
Pulls full tournament data from melee.gg using the same approach
proven in the existing swu-tracker project.

Flow (hub source):
  1. swu-competitivehub.com/tournaments-results/  -- get Premier LAW event list
  2. hub event page                               -- extract melee tournament ID
  3. melee.gg/Tournament/View/{id}               -- parse round button IDs from HTML
  4. POST /Standing/GetRoundStandings            -- full standings (all players)
  5. POST /Match/GetRoundMatches/{round_id}      -- all pairings per round
  6. GET  /Decklist/View/{uuid}                  -- card list per deck (optional)

Flow (SWU API source, preferred):
  1. admin.starwarsunlimited.com/api/event-search  -- official event list w/ melee URLs
  2. Steps 3-6 above (melee scraping is the same)

Usage:
    python -m scraper.melee                       # all Premier LAW events (hub)
    python -m scraper.melee --swu                 # all Premier PQ events (SWU API)
    python -m scraper.melee --limit 20            # most recent 20
    python -m scraper.melee --melee-id 408083     # single tournament by melee ID
    python -m scraper.melee --cards               # also fetch full decklists
    python -m scraper.melee --refresh-views       # just refresh materialized views
    python -m scraper.melee --eternal             # Eternal format events
    python -m scraper.melee --eternal --melee-id 123  # single Eternal tournament
"""

import argparse
import logging
import re
import time
from typing import Optional

from bs4 import BeautifulSoup
from tenacity import retry, stop_after_attempt, wait_exponential

import db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)

HUB_BASE = "https://www.swu-competitivehub.com"
SWU_API  = "https://admin.starwarsunlimited.com"
MELEE    = "https://melee.gg"

# Event type IDs on starwarsunlimited.com
SWU_TYPE_PQ = 4   # Planetary Qualifier
SWU_TYPE_SQ = 5   # Sector Qualifier
SWU_TYPE_GC = 3   # Galactic Championship
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept":                    "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language":           "en-US,en;q=0.9",
    "Accept-Encoding":           "gzip, deflate, br",
    "Referer":                   "https://melee.gg/",
    "Sec-Fetch-Dest":            "document",
    "Sec-Fetch-Mode":            "navigate",
    "Sec-Fetch-Site":            "same-origin",
    "Upgrade-Insecure-Requests": "1",
    "Cache-Control":             "no-cache",
    "Pragma":                    "no-cache",
}
SLEEP = 0.8

# DataTables column definitions for melee POST endpoints
STANDINGS_COLUMNS = [
    "Rank", "Player", "Decklists", "MatchRecord", "GameRecord",
    "Points", "OpponentMatchWinPercentage", "TeamGameWinPercentage",
    "OpponentGameWinPercentage", "FinalTiebreaker", "OpponentCount",
]
MATCHES_COLUMNS = [
    "TableNumber", "PodNumber", "Teams", "Decklists", "ResultString",
]


# ── HTTP ───────────────────────────────────────────────────────────────────

_session = None

def _get_session():
    global _session
    if _session is None:
        import requests as req
        _session = req.Session()
        _session.headers.update(HEADERS)
    return _session

@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=4, max=15))
def get(url: str) -> BeautifulSoup:
    sess = _get_session()
    resp = sess.get(url, timeout=25, allow_redirects=True)
    if resp.status_code == 403:
        # Cloudflare or bot check — wait longer before retry
        time.sleep(5)
        resp = sess.get(url, timeout=25, allow_redirects=True)
    resp.raise_for_status()
    time.sleep(SLEEP)
    return BeautifulSoup(resp.text, "lxml")


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=4, max=15))
def post_json(url: str, data: dict) -> dict:
    sess = _get_session()
    resp = sess.post(
        url, data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded",
                 "X-Requested-With": "XMLHttpRequest"},
        timeout=30,
    )
    resp.raise_for_status()
    time.sleep(SLEEP)
    return resp.json()


def _dt_params(columns: list, round_id: int, length: int = 500) -> dict:
    """Build DataTables POST body that melee.gg expects."""
    p = {
        "draw": "1", "start": "0", "length": str(length),
        "search[value]": "", "search[regex]": "false",
        "order[0][column]": "0", "order[0][dir]": "asc",
        "roundId": str(round_id),
    }
    for i, col in enumerate(columns):
        p[f"columns[{i}][data]"]          = col
        p[f"columns[{i}][name]"]          = col
        p[f"columns[{i}][searchable]"]    = "true"
        p[f"columns[{i}][orderable]"]     = "true"
        p[f"columns[{i}][search][value]"] = ""
        p[f"columns[{i}][search][regex]"] = "false"
    return p


# ── Table name helpers ─────────────────────────────────────────────────────

def _table_names(eternal: bool = False) -> dict:
    """Return the correct DB table names for the given format."""
    p = "eternal_" if eternal else ""
    return {
        "events":         f"{p}events",
        "standings":      f"{p}standings",
        "decklist_cards": f"{p}decklist_cards",
        "matches":        f"{p}matches",
    }


# ── Hub: event list ────────────────────────────────────────────────────────

def hub_event_list(set_code: str = "LAW", limit: int = 0,
                   eternal: bool = False) -> list[dict]:
    """
    Scrape the hub tournament results page.
    Premier mode: filters to target set + PQ/SQ/RQ level events.
    Eternal mode: uses the eternal category URL, no set/level filtering.
    Returns list of stubs with: name, date, hub_url, country, players, set_code.
    """
    if eternal:
        url = f"{HUB_BASE}/tournaments-results/?range=all&category=eternal"
    else:
        url = f"{HUB_BASE}/tournaments-results/?range=all&category=premier"
    log.info(f"Fetching hub event list: {url}")
    soup = get(url)

    table = soup.find("table")
    if not table:
        log.error("No table found on hub results page")
        return []

    events = []
    for row in table.find_all("tr")[1:]:
        cells = row.find_all("td")
        if len(cells) < 6:
            continue

        if not eternal:
            level   = cells[4].get_text(strip=True)
            set_txt = cells[6].get_text(strip=True) if len(cells) > 6 else ""

            # Filter: target set, Premier only (no Limited/Eternal)
            if set_txt.upper() != set_code.upper():
                continue
            if "Limited" in level or "Eternal" in level:
                continue
            # Only PQ / SQ / RQ level events (skip Minor Tournaments)
            if not any(t in level for t in ("Planetary Qualifier", "Sector Qualifier",
                                             "Regional Qualifier", "Galactic Championship")):
                continue

        a = cells[1].find("a")
        if not a:
            continue

        players_txt = cells[5].get_text(strip=True)
        level_txt   = cells[4].get_text(strip=True) if len(cells) > 4 else ""
        events.append({
            "name":         a.get_text(strip=True),
            "date":         cells[0].get_text(strip=True),
            "hub_url":      a["href"],
            "country":      cells[3].get_text(strip=True),
            "player_count": int(players_txt) if players_txt.isdigit() else None,
            "event_level":  level_txt,
            "set_code":     set_code if not eternal else None,
        })

        if limit and len(events) >= limit:
            break

    fmt_label = "Eternal" if eternal else f"Premier {set_code}"
    log.info(f"  {len(events)} {fmt_label} events found")
    return events


def hub_event_page(hub_url: str) -> dict:
    """
    Fetch a hub event detail page.
    Returns: melee_id, melee_url, name, venue, country, set_code.
    This is the only thing we need from the hub -- the melee tournament ID.
    """
    log.info(f"  Hub page: {hub_url}")
    soup = get(hub_url)
    text = soup.get_text(" ", strip=True)
    result = {"hub_url": hub_url}

    h1 = soup.find("h1")
    result["name"] = h1.get_text(strip=True) if h1 else ""

    # Melee tournament link -- the key piece of data
    a = soup.find("a", href=re.compile(r"melee\.gg/Tournament/View/(\d+)"))
    if a:
        result["melee_url"] = a["href"]
        m = re.search(r"/(\d+)$", a["href"])
        result["melee_id"]  = m.group(1) if m else None
    else:
        result["melee_id"]  = None
        result["melee_url"] = ""

    # Set code
    m = re.search(r"\bSet\s+([A-Z]{2,4})\b", text)
    result["set_code"] = m.group(1) if m else ""

    # Venue / location
    m = re.search(r"Location[:\s]+(.+?)(?:Structure:|$)", text, re.DOTALL)
    if m:
        raw   = re.sub(r"\s+", " ", m.group(1)).strip()
        parts = [p.strip() for p in re.split(r"\s*/\s*", raw) if p.strip()]
        result["venue"]    = parts[0] if parts else ""
        result["location"] = ", ".join(parts[:3])

    return result


# ── Melee: tournament page → round IDs ────────────────────────────────────

def melee_tournament_rounds(melee_id: str) -> dict:
    """
    GET the tournament HTML page.
    The standings/matches rows are loaded by AJAX -- not in the HTML.
    But the round button IDs ARE in the HTML (data-id attributes).
    Returns: {meta, standings_rounds, pairings_rounds}
    """
    url = f"{MELEE}/Tournament/View/{melee_id}"
    log.info(f"  Melee page: {url}")
    soup = get(url)
    text = soup.get_text(" ", strip=True)

    # Player count
    m = re.search(r"(\d+)\s+of\s+\d+\s+Enrolled Players", text)
    player_count = int(m.group(1)) if m else None

    m = re.search(r"Format:\s*(\w+)", text)
    fmt = m.group(1) if m else "Premier"

    # Event date from the first <span data-value="YYYY-MM-DDT...Z"> element
    date_str = None
    dt_el = soup.find(attrs={"data-value": re.compile(r"\d{4}-\d{2}-\d{2}T")})
    if dt_el:
        date_str = dt_el["data-value"][:10]  # "YYYY-MM-DD"

    def parse_round_btns(container) -> list[dict]:
        if not container:
            return []
        rounds = []
        for btn in container.find_all("button", attrs={"data-id": True}):
            rounds.append({
                "id":        int(btn["data-id"]),
                "name":      btn.get("data-name", btn.get_text(strip=True)),
                "completed": btn.get("data-is-completed", "").lower() == "true",
                "started":   btn.get("data-is-started",  "").lower() == "true",
            })
        return rounds

    standings_rounds = parse_round_btns(
        soup.find(id="standings-round-selector-container")
    )
    pairings_rounds = parse_round_btns(
        soup.find(id="pairings-round-selector-container")
    )

    log.info(f"  Standings rounds: {[r['name'] for r in standings_rounds]}")
    log.info(f"  Pairings rounds:  {[r['name'] for r in pairings_rounds]}")

    return {
        "meta": {"player_count": player_count, "format": fmt, "date": date_str},
        "standings_rounds": standings_rounds,
        "pairings_rounds":  pairings_rounds,
    }


# ── Melee: standings ───────────────────────────────────────────────────────

def melee_round_standings(round_id: int) -> list[dict]:
    """POST GetRoundStandings → raw row list."""
    url  = f"{MELEE}/Standing/GetRoundStandings"
    data = _dt_params(STANDINGS_COLUMNS, round_id, length=500)
    resp = post_json(url, data)
    return resp.get("data", [])


def parse_standing_row(row: dict) -> dict:
    """Normalise a raw GetRoundStandings row."""
    players  = row.get("Team", {}).get("Players", [])
    p0       = players[0] if players else {}
    decklists = row.get("Decklists", [])
    dl        = decklists[0] if decklists else {}
    deck_id   = dl.get("DecklistId", "")
    leader, base = _parse_deck_name(dl.get("DecklistName", ""))
    return {
        "place":           row.get("Rank"),
        "player_name":     p0.get("DisplayName") or p0.get("Username", ""),
        "melee_username":  p0.get("UserSlug") or p0.get("Username", ""),
        "melee_player_id": str(p0.get("ID", "")),
        "melee_deck_id":   deck_id,
        "deck_name":       dl.get("DecklistName", ""),
        "leader":          leader,
        "base":            base,
        "melee_deck_url":  f"{MELEE}/Decklist/View/{deck_id}" if deck_id else "",
        "swiss_record":    row.get("MatchRecord", ""),
        "game_record":     row.get("GameRecord", ""),
        "swiss_points":    row.get("Points"),
        "omw_pct":         row.get("OpponentMatchWinPercentage"),
        "tgw_pct":         row.get("TeamGameWinPercentage"),
        "ogw_pct":         row.get("OpponentGameWinPercentage"),
    }


# ── Melee: matches ─────────────────────────────────────────────────────────

def melee_round_matches(round_id: int, include_byes: bool = False) -> list[dict]:
    """POST GetRoundMatches → parsed match list."""
    url  = f"{MELEE}/Match/GetRoundMatches/{round_id}"
    data = _dt_params(MATCHES_COLUMNS, round_id, length=500)
    try:
        resp = post_json(url, data)
        return _parse_matches(resp.get("data", []), include_byes=include_byes)
    except Exception as e:
        log.warning(f"    Matches failed for round {round_id}: {e}")
        return []


def _parse_matches(raw: list, include_byes: bool = False) -> list[dict]:
    matches = []
    for m in raw:
        competitors = m.get("Competitors", [])
        is_bye = m.get("ByeReason") not in (None, 0, "0") or len(competitors) < 2

        if is_bye:
            if include_byes and competitors:
                players = competitors[0].get("Team", {}).get("Players", [])
                p = players[0] if players else {}
                name = p.get("DisplayName") or p.get("Username", "")
                if name:
                    matches.append({
                        "p1_name": name, "p1_melee_id": str(p.get("ID", "")),
                        "p1_deck_id": "", "p1_deck_name": "", "p1_game_wins": 2,
                        "p2_name": "BYE", "p2_melee_id": "",
                        "p2_deck_id": "", "p2_deck_name": "", "p2_game_wins": 0,
                        "game_draws": 0, "winner": "p1",
                        "result_str": m.get("ResultString", "BYE"),
                        "match_guid": m.get("Guid", ""), "phase_id": m.get("PhaseId"),
                    })
            continue

        if not m.get("HasResult"):
            continue

        def extract(c):
            players = c.get("Team", {}).get("Players", [])
            p  = players[0] if players else {}
            dls = c.get("Decklists", [])
            dl  = dls[0] if dls else {}
            return {
                "player_name":  p.get("DisplayName") or p.get("Username", ""),
                "melee_id":     str(p.get("ID", "")),
                "deck_id":      dl.get("DecklistId", ""),
                "deck_name":    dl.get("DecklistName", ""),
                "game_wins":    (c.get("GameWins") or 0) + (c.get("GameByes") or 0),
            }

        c1 = extract(competitors[0])
        c2 = extract(competitors[1])

        if   c1["game_wins"] > c2["game_wins"]: winner = "p1"
        elif c2["game_wins"] > c1["game_wins"]: winner = "p2"
        else:                                   winner = "draw"

        matches.append({
            "p1_name":      c1["player_name"],
            "p1_melee_id":  c1["melee_id"],
            "p1_deck_id":   c1["deck_id"],
            "p1_deck_name": c1["deck_name"],
            "p1_game_wins": c1["game_wins"],
            "p2_name":      c2["player_name"],
            "p2_melee_id":  c2["melee_id"],
            "p2_deck_id":   c2["deck_id"],
            "p2_deck_name": c2["deck_name"],
            "p2_game_wins": c2["game_wins"],
            "game_draws":   m.get("GameDraws") or 0,
            "winner":       winner,
            "result_str":   m.get("ResultString", ""),
            "match_guid":   m.get("Guid", ""),
            "phase_id":     m.get("PhaseId"),
        })
    return matches


# ── Melee: decklists ───────────────────────────────────────────────────────

def melee_decklist_cards(deck_id: str) -> list[dict]:
    """
    GET /Decklist/View/{uuid} → parse card list.
    Returns [{card_name, quantity, is_sideboard}].
    """
    url = f"{MELEE}/Decklist/View/{deck_id}"
    log.debug(f"    Decklist: {url}")
    try:
        soup = get(url)
    except Exception as e:
        log.warning(f"    Could not fetch decklist {deck_id}: {e}")
        return []

    # Try <pre> text export first
    pre = soup.find("pre")
    if pre:
        return _parse_text_decklist(pre.get_text())

    # Structured card rows
    cards    = []
    sideboard = False
    for el in soup.find_all(["div", "li", "tr"],
                             class_=re.compile(r"card|deck", re.I)):
        text = el.get_text(" ", strip=True)
        if "sideboard" in text.lower():
            sideboard = True
            continue
        m = re.match(r"^(\d+)\s*[xX]?\s*(.+)$", text)
        if m:
            cards.append({
                "card_name":   m.group(2).strip(),
                "quantity":    int(m.group(1)),
                "is_sideboard": sideboard,
            })
    return cards


def _parse_text_decklist(text: str) -> list[dict]:
    cards    = []
    sideboard = False
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.lower().startswith("sideboard"):
            sideboard = True
            continue
        m = re.match(r"^(\d+)\s*[xX]?\s*(.+)$", line)
        if m:
            cards.append({
                "card_name":   m.group(2).strip(),
                "quantity":    int(m.group(1)),
                "is_sideboard": sideboard,
            })
    return cards


# ── DB helpers ─────────────────────────────────────────────────────────────

def upsert_event(ev: dict, tbl: str = "events") -> int:
    melee_id = ev["melee_id"]
    set_code = ev.get("set_code") or None  # normalize "" to None to avoid FK violation
    row = db.fetchone(f"SELECT id FROM {tbl} WHERE melee_id = %s", (melee_id,))
    if row:
        db.execute(
            f"""UPDATE {tbl}
               SET name=%s, date=%s, player_count=%s, set_code=%s,
                   melee_url=%s, venue=%s, country=%s
               WHERE id=%s""",
            (ev.get("name"), ev.get("date"), ev.get("player_count"),
             set_code, ev.get("melee_url"),
             ev.get("venue"), ev.get("country"), row["id"])
        )
        return row["id"]
    else:
        r = db.fetchone(
            f"""INSERT INTO {tbl}
               (melee_id, name, date, player_count, set_code, melee_url, venue, country)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
            (melee_id, ev.get("name"), ev.get("date"), ev.get("player_count"),
             set_code, ev.get("melee_url"),
             ev.get("venue"), ev.get("country"))
        )
        return r["id"]


def upsert_standing(event_id: int, st: dict, tbl: str = "standings") -> int:
    melee_player_id = st.get("melee_player_id") or st.get("melee_deck_id") or str(st["place"])

    # Parse match/game records like "7-0-0"
    mw, ml, md = _parse_record(st.get("swiss_record", ""))
    gw, gl, _  = _parse_record(st.get("game_record", ""))
    mwr = round(mw / (mw + ml + md), 4) if (mw + ml + md) > 0 else None
    gwr = round(gw / (gw + gl),      4) if (gw + gl) > 0 else None

    row = db.fetchone(
        f"SELECT id FROM {tbl} WHERE event_id=%s AND melee_player_id=%s",
        (event_id, melee_player_id)
    )

    username = st.get("melee_username") or ""
    fields = dict(
        player_name      = st.get("player_name"),
        placement        = st.get("place"),
        leader           = st.get("leader"),
        base             = st.get("base"),
        decklist_url     = st.get("melee_deck_url"),
        has_decklist     = bool(st.get("melee_deck_id")),
        match_wins       = mw or None,
        match_losses     = ml or None,
        match_draws      = md or None,
        match_win_rate   = mwr,
        game_wins        = gw or None,
        game_losses      = gl or None,
        game_win_rate    = gwr,
        points           = st.get("swiss_points"),
        omwp             = st.get("omw_pct"),
        tgwp             = st.get("tgw_pct"),
        ogwp             = st.get("ogw_pct"),
        melee_profile_url = f"https://melee.gg/Profile/Index/{username}" if username else None,
    )

    if row:
        set_clause = ", ".join(f"{k}=%s" for k in fields)
        db.execute(
            f"UPDATE {tbl} SET {set_clause} WHERE id=%s",
            list(fields.values()) + [row["id"]]
        )
        return row["id"]
    else:
        cols  = ", ".join(["event_id", "melee_player_id"] + list(fields.keys()))
        phs   = ", ".join(["%s"] * (2 + len(fields)))
        r = db.fetchone(
            f"INSERT INTO {tbl} ({cols}) VALUES ({phs}) RETURNING id",
            [event_id, melee_player_id] + list(fields.values())
        )
        return r["id"]


def save_match(event_id: int, round_id: int, round_name: str,
               round_num: int, match: dict, tbls: dict = None):
    """Upsert a match row. Requires a matches table — see schema note."""
    if tbls is None:
        tbls = _table_names(eternal=False)
    if not _matches_table_exists(tbls["matches"]):
        return
    p1_standing = db.fetchone(
        f"SELECT id, leader, base FROM {tbls['standings']} WHERE event_id=%s AND melee_player_id=%s",
        (event_id, match["p1_melee_id"])
    )
    p2_standing = db.fetchone(
        f"SELECT id, leader, base FROM {tbls['standings']} WHERE event_id=%s AND melee_player_id=%s",
        (event_id, match["p2_melee_id"])
    )
    guid = match.get("match_guid")
    if guid:
        existing = db.fetchone(
            f"SELECT id FROM {tbls['matches']} WHERE match_guid=%s", (guid,)
        )
        if existing:
            return

    # Parse leader/base from deck names directly — most reliable source
    # Falls back to standings lookup if deck name is missing
    _p1l, _p1b = _parse_deck_name(match.get("p1_deck_name", ""))
    _p2l, _p2b = _parse_deck_name(match.get("p2_deck_name", ""))
    p1_leader = _p1l or (p1_standing.get("leader") if p1_standing else None)
    p1_base   = _p1b or (p1_standing.get("base")   if p1_standing else None)
    p2_leader = _p2l or (p2_standing.get("leader") if p2_standing else None)
    p2_base   = _p2b or (p2_standing.get("base")   if p2_standing else None)

    if not p1_leader or not p2_leader:
        log.debug(f"  Match missing leader: p1_deck={match.get('p1_deck_name')!r} "
                  f"p2_deck={match.get('p2_deck_name')!r} "
                  f"p1_standing={'found' if p1_standing else 'missing'} "
                  f"p2_standing={'found' if p2_standing else 'missing'}")

    db.execute(
        f"""INSERT INTO {tbls['matches']}
           (event_id, melee_round_id, round_num, round_name, round_type,
            p1_standing_id, p2_standing_id,
            p1_leader, p1_base, p2_leader, p2_base,
            p1_name, p1_melee_id, p1_deck_id, p1_deck_name, p1_game_wins,
            p2_name, p2_melee_id, p2_deck_id, p2_deck_name, p2_game_wins,
            game_draws, winner, result_str, match_guid, phase_id)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
           ON CONFLICT (match_guid) DO NOTHING""",
        (event_id, round_id, round_num, round_name, _round_type(round_name),
         p1_standing["id"] if p1_standing else None,
         p2_standing["id"] if p2_standing else None,
         p1_leader, p1_base, p2_leader, p2_base,
         match["p1_name"], match["p1_melee_id"], match["p1_deck_id"],
         match["p1_deck_name"], match["p1_game_wins"],
         match["p2_name"], match["p2_melee_id"], match["p2_deck_id"],
         match["p2_deck_name"], match["p2_game_wins"],
         match["game_draws"], match["winner"], match["result_str"],
         match.get("match_guid"), match.get("phase_id"))
    )


def save_decklist(standing_id: int, cards: list[dict], tbl: str = "decklist_cards"):
    db.execute(f"DELETE FROM {tbl} WHERE standing_id=%s", (standing_id,))
    for c in cards:
        db.execute(
            f"""INSERT INTO {tbl} (standing_id, card_name, quantity, is_sideboard)
               VALUES (%s,%s,%s,%s)""",
            (standing_id, c["card_name"], c["quantity"], c["is_sideboard"])
        )


# ── Full tournament import ─────────────────────────────────────────────────

def import_tournament(
    melee_id:     str,
    event_meta:   dict        = None,
    fetch_cards:  bool        = False,
    eternal:      bool        = False,
) -> Optional[dict]:
    """
    Full pipeline for one melee tournament:
    1. GET tournament page -> round IDs + player count
    2. POST GetRoundStandings (final Swiss round) -> all players
    3. POST GetRoundMatches per round -> all match results
    4. Optionally GET each decklist for cards
    """
    log.info(f"  Importing melee tournament {melee_id}...")
    tbls = _table_names(eternal)

    # Step 1: round IDs from HTML
    page = melee_tournament_rounds(melee_id)
    standings_rounds = page["standings_rounds"]
    pairings_rounds  = page["pairings_rounds"]
    meta             = page["meta"]

    if not standings_rounds:
        log.warning("  No rounds found -- tournament may be private or incomplete")
        return None

    # Merge player count from melee page into event record
    if meta.get("player_count") and event_meta:
        event_meta["player_count"] = event_meta.get("player_count") or meta["player_count"]

    ev_id = upsert_event({
        "melee_id":     melee_id,
        "name":         (event_meta or {}).get("name", f"Melee {melee_id}"),
        "date":         (event_meta or {}).get("date"),
        "player_count": meta.get("player_count") or (event_meta or {}).get("player_count"),
        "set_code":     (event_meta or {}).get("set_code"),
        "melee_url":    f"{MELEE}/Tournament/View/{melee_id}",
        "venue":        (event_meta or {}).get("venue"),
        "country":      (event_meta or {}).get("country"),
    }, tbl=tbls["events"])

    # Step 2: standings from the best available round.
    # Try rounds in priority order; fall back if a round returns empty standings.
    elim_order = {"final": 3, "semifinal": 2, "quarterfinal": 1, "top ": 0}

    def elim_priority(r):
        return max(
            (v for k, v in elim_order.items() if r["name"].lower().startswith(k)),
            default=-1
        )

    # Candidates: completed elim rounds (best first), then completed swiss (last first)
    completed = [r for r in standings_rounds if r["completed"]]
    elim_rounds = sorted(
        [r for r in completed if elim_priority(r) >= 0],
        key=elim_priority, reverse=True
    )
    swiss_rounds = [r for r in completed if elim_priority(r) < 0]

    candidates = elim_rounds + list(reversed(swiss_rounds))
    if not candidates:
        candidates = list(reversed(standings_rounds))

    standings = []
    final_round = None
    for candidate in candidates:
        raw_standings = melee_round_standings(candidate["id"])
        standings = [parse_standing_row(r) for r in raw_standings]
        if standings:
            if final_round is not None:
                log.info(f"  Falling back from empty round — using {candidate['name']}")
            final_round = candidate
            break
        log.info(f"  {candidate['name']} returned 0 standings, trying next round…")
        final_round = candidate  # track last tried for logging

    log.info(f"  Standings from: {final_round['name']} (id={final_round['id']})")
    log.info(f"  {len(standings)} players")

    if not standings:
        log.warning("  No standings returned")
        return None

    # Upsert all standings
    standing_id_map = {}  # melee_player_id -> our standing.id
    for st in standings:
        st_id = upsert_standing(ev_id, st, tbl=tbls["standings"])
        standing_id_map[st.get("melee_player_id", "")] = st_id

    # Step 3: matches for every round
    total_matches = 0
    for i, rnd in enumerate(pairings_rounds):
        if not (rnd.get("started") or rnd.get("completed")):
            continue
        log.info(f"  Matches: {rnd['name']} (id={rnd['id']})")
        matches = melee_round_matches(rnd["id"])
        for m in matches:
            save_match(ev_id, rnd["id"], rnd["name"], i + 1, m, tbls=tbls)
        total_matches += len(matches)
        log.info(f"    {len(matches)} matches")

    log.info(f"  Total matches: {total_matches}")

    # Update player count now that we have full standings
    db.execute(
        f"UPDATE {tbls['events']} SET player_count=%s WHERE id=%s AND (player_count IS NULL OR player_count < %s)",
        (len(standings), ev_id, len(standings))
    )

    # Step 4: decklists (optional)
    if fetch_cards:
        deck_ids = {s["melee_deck_id"] for s in standings if s.get("melee_deck_id")}
        log.info(f"  Fetching {len(deck_ids)} decklists...")
        for deck_id in deck_ids:
            # Find the standing for this deck
            st = next((s for s in standings if s.get("melee_deck_id") == deck_id), None)
            if not st:
                continue
            st_id = standing_id_map.get(st.get("melee_player_id", ""))
            if not st_id:
                continue
            cards = melee_decklist_cards(deck_id)
            if cards:
                save_decklist(st_id, cards, tbl=tbls["decklist_cards"])
                log.debug(f"    {deck_id}: {len(cards)} cards")

    return {"event_id": ev_id, "standings": len(standings), "matches": total_matches}


# ── SWU Official API ───────────────────────────────────────────────────────

def swu_api_event_list(
    event_type_ids: list = None,
    since_ms:       int  = 0,
    eternal:        bool = False,
    limit:          int  = 0,
) -> list[dict]:
    """
    Fetch events from the official SWU Strapi API.
    Returns stubs with melee_id already extracted:
      {name, date, melee_id, melee_url, format, country, city, venue, event_type}

    event_type_ids: list of SWU type IDs (default [SWU_TYPE_PQ])
    since_ms: Unix timestamp in milliseconds for startDate filter (0 = no filter)
    eternal: if True, only return Eternal-format events
    """
    import urllib.parse

    if event_type_ids is None:
        event_type_ids = [SWU_TYPE_PQ]

    sess = _get_session()
    all_events = []

    for type_id in event_type_ids:
        page = 1
        while True:
            params = {
                "locale":                                      "en",
                "populate[0]":                                 "*",
                "pagination[pageSize]":                        100,
                "pagination[page]":                            page,
                "sort[0]":                                     "startDate:desc",
                "filters[$and][0][type][id][$eq]":             type_id,
            }
            if since_ms:
                params["filters[$and][1][startDate][$gte]"] = since_ms

            log.info(f"  SWU API type={type_id} page={page} …")
            try:
                resp = sess.get(
                    f"{SWU_API}/api/event-search",
                    params=params,
                    headers={
                        "Accept":          "application/json",
                        "Accept-Encoding": "gzip, deflate",  # avoid Brotli
                        "Origin":          "https://starwarsunlimited.com",
                        "Referer":         "https://starwarsunlimited.com/",
                    },
                    timeout=20,
                )
                resp.raise_for_status()
                data = resp.json()
            except Exception as e:
                log.error(f"  SWU API request failed: {e}")
                break

            items      = data.get("data", [])
            pagination = data.get("meta", {}).get("pagination", {})

            for e in items:
                attrs     = e.get("attributes", {})
                melee_url = attrs.get("url", "") or ""

                if "melee.gg" not in melee_url:
                    continue

                m = re.search(r"/(\d+)$", melee_url)
                if not m:
                    continue
                melee_id = m.group(1)

                fmt_name = (
                    attrs.get("format", {})
                         .get("data", {})
                         .get("attributes", {})
                         .get("name", "Premier")
                )
                is_eternal = fmt_name.lower() == "eternal"
                if eternal and not is_eternal:
                    continue
                if not eternal and is_eternal:
                    continue

                loc       = attrs.get("location", {}).get("data", {})
                loc_attrs = loc.get("attributes", {}) if loc else {}
                address   = loc_attrs.get("address") or {}

                all_events.append({
                    "name":       attrs.get("name", ""),
                    "date":       (attrs.get("startDate", "") or "")[:10],
                    "melee_id":   melee_id,
                    "melee_url":  melee_url,
                    "format":     "eternal" if is_eternal else "standard",
                    "country":    address.get("country"),
                    "city":       address.get("city"),
                    "venue":      loc_attrs.get("name"),
                    "event_type": (
                        attrs.get("type", {})
                             .get("data", {})
                             .get("attributes", {})
                             .get("name")
                    ),
                })

                if limit and len(all_events) >= limit:
                    break

            log.info(f"    {len(items)} returned, {len(all_events)} total with melee links so far")

            if limit and len(all_events) >= limit:
                break
            if page >= pagination.get("pageCount", 1):
                break
            page += 1
            time.sleep(SLEEP)

    return all_events[:limit] if limit else all_events


def sync_from_swu(
    event_type_ids: list = None,
    fetch_cards:    bool = False,
    since_days:     int  = 0,
    eternal:        bool = False,
    limit:          int  = 0,
):
    """
    Sync Premier (or Eternal) events from the official SWU API.
    The API provides the melee URL directly — no hub scraping needed.
    """
    from datetime import date as _date, timedelta

    if event_type_ids is None:
        event_type_ids = [SWU_TYPE_PQ]

    since_ms = 0
    if since_days > 0:
        cutoff   = _date.today() - timedelta(days=since_days)
        since_ms = int(cutoff.strftime("%s")) * 1000
        log.info(f"--days {since_days}: fetching events since {cutoff}")

    tbls   = _table_names(eternal)

    # Look up current meta's set_code to tag upcoming event stubs
    try:
        fmt_col = "eternal" if eternal else "premiere"
        cur_meta = db.fetchone(
            "SELECT set_code FROM metas WHERE is_current = TRUE AND format = %s LIMIT 1",
            (fmt_col,)
        )
        current_set_code = cur_meta["set_code"] if cur_meta else None
    except Exception:
        current_set_code = None
    if current_set_code:
        log.info(f"Current meta set_code: {current_set_code}")

    stubs  = swu_api_event_list(
        event_type_ids=event_type_ids,
        since_ms=since_ms,
        eternal=eternal,
        limit=limit,
    )

    if not stubs:
        log.warning("No events with melee links found via SWU API")
        return

    log.info(f"{len(stubs)} events with melee links to process")
    ok = fail = skipped = 0

    for i, stub in enumerate(stubs, 1):
        melee_id = stub["melee_id"]
        log.info(f"[{i}/{len(stubs)}] {stub['name']} ({stub.get('date','?')})  melee={melee_id}")

        try:
            event_date = stub.get("date")
            event_meta = {
                "name":         stub["name"],
                "date":         event_date,
                "player_count": None,
                "set_code":     current_set_code,
                "melee_url":    stub["melee_url"],
                "venue":        stub.get("venue"),
                "country":      stub.get("country"),
            }

            # Upcoming event — upsert the stub so it appears in the events table,
            # but don't attempt to scrape melee (no results yet).
            is_future = False
            if event_date:
                try:
                    is_future = _date.fromisoformat(event_date) > _date.today()
                except ValueError:
                    pass

            if is_future:
                event_meta["set_code"] = current_set_code
                upsert_event({"melee_id": melee_id, **event_meta}, tbl=tbls["events"])
                log.info(f"  Upcoming — stub upserted")
                skipped += 1
                continue

            # Skip if already fully scraped
            existing = db.fetchone(
                f"""SELECT e.id, COUNT(s.id) AS n
                   FROM {tbls['events']} e
                   LEFT JOIN {tbls['standings']} s ON s.event_id = e.id
                   WHERE e.melee_id = %s
                   GROUP BY e.id""",
                (melee_id,)
            )
            if existing and (existing["n"] or 0) > 0:
                log.info(f"  Already scraped ({existing['n']} standings) -- skipping")
                skipped += 1
                continue

            result = import_tournament(melee_id, event_meta, fetch_cards, eternal=eternal)
            if result:
                log.info(f"  Done: {result['standings']} standings, {result['matches']} matches")
                ok += 1
            else:
                fail += 1

        except Exception as e:
            log.error(f"  Failed: {e}")
            import traceback; traceback.print_exc()
            fail += 1

    log.info(f"\nSync complete: {ok} imported, {fail} failed, {skipped} skipped")

    log.info("Refreshing materialized views...")
    if eternal:
        db.execute_autocommit("SELECT refresh_eternal_views()")
    else:
        db.execute_autocommit("SELECT refresh_all_views()")
        _refresh_player_identities()
    log.info("Done.")


# ── Sync from hub ──────────────────────────────────────────────────────────

def sync_from_hub(
    set_code:    str  = "LAW",
    limit:       int  = 0,
    fetch_cards: bool = False,
    since_days:  int  = 0,   # 0 = no limit
    eternal:     bool = False,
):
    """
    1. Get event list from hub (Premier or Eternal)
    2. For each event, fetch hub page to get the melee tournament ID
    3. Run full import_tournament()
    Skip events that already have standings in the DB.
    If since_days > 0, only process events from the last N days.
    """
    tbls  = _table_names(eternal)
    stubs = hub_event_list(set_code=set_code, limit=limit, eternal=eternal)
    if not stubs:
        log.warning("No events found on hub")
        return

    # Apply date window filter before doing any per-event HTTP requests
    if since_days > 0:
        from datetime import date as _date, timedelta
        cutoff = _date.today() - timedelta(days=since_days)
        before = len(stubs)
        stubs = [
            s for s in stubs
            if s.get("date") and _date.fromisoformat(s["date"]) >= cutoff
        ]
        log.info(f"--days {since_days}: keeping {len(stubs)}/{before} events since {cutoff}")
        if not stubs:
            log.info("No events in window — done.")
            return

    ok = fail = skipped = 0

    for i, stub in enumerate(stubs, 1):
        log.info(f"[{i}/{len(stubs)}] {stub['name']} ({stub.get('date','?')})")

        try:
            hub_data = hub_event_page(stub["hub_url"])
            melee_id = hub_data.get("melee_id")

            if not melee_id:
                log.warning("  No melee ID found -- skipping")
                skipped += 1
                continue

            # Skip future events
            event_date = stub.get("date")
            if event_date:
                try:
                    from datetime import date as _date
                    if _date.fromisoformat(event_date) > _date.today():
                        log.info(f"  Future event ({event_date}) -- skipping")
                        skipped += 1
                        continue
                except ValueError:
                    pass

            # Skip if already fully scraped
            existing = db.fetchone(
                f"""SELECT e.id, COUNT(s.id) AS n
                   FROM {tbls['events']} e
                   LEFT JOIN {tbls['standings']} s ON s.event_id = e.id
                   WHERE e.melee_id = %s
                   GROUP BY e.id""",
                (melee_id,)
            )
            if existing and (existing["n"] or 0) > 0:
                log.info(f"  Already scraped ({existing['n']} standings) -- skipping")
                skipped += 1
                continue

            event_meta = {
                "name":        stub.get("name") or hub_data.get("name"),
                "date":        stub.get("date"),
                "player_count": stub.get("player_count"),
                "set_code":    None if eternal else (stub.get("set_code") or hub_data.get("set_code")),
                "venue":       hub_data.get("venue"),
                "country":     stub.get("country") or hub_data.get("country"),
            }

            result = import_tournament(melee_id, event_meta, fetch_cards, eternal=eternal)
            if result:
                log.info(f"  Done: {result['standings']} standings, {result['matches']} matches")
                ok += 1
            else:
                fail += 1

        except Exception as e:
            log.error(f"  Failed: {e}")
            import traceback; traceback.print_exc()
            fail += 1

    log.info(f"\nSync complete: {ok} imported, {fail} failed, {skipped} skipped")

    log.info("Refreshing materialized views...")
    if eternal:
        db.execute_autocommit("SELECT refresh_eternal_views()")
    else:
        db.execute_autocommit("SELECT refresh_all_views()")
        _refresh_player_identities()
    log.info("Done.")


# ── Helpers ────────────────────────────────────────────────────────────────

def _refresh_player_identities():
    """
    Assign stable identities to any melee_player_ids not yet in player_id_map.
    - New ID, name not seen before → create new identity (confirmed)
    - New ID, name matches existing confirmed identity, no tournament overlap → merge (confirmed)
    - New ID, name matches but tournament overlap → create new identity, flag for review
    """
    try:
        # Find melee_player_ids not yet mapped
        unmapped = db.fetchall("""
            SELECT
                s.melee_player_id,
                s.player_name,
                e.event_ids
            FROM (
                SELECT DISTINCT ON (melee_player_id)
                    melee_player_id,
                    player_name
                FROM standings
                WHERE melee_player_id IS NOT NULL
                  AND player_name IS NOT NULL AND player_name != ''
                  AND NOT EXISTS (
                      SELECT 1 FROM player_id_map m WHERE m.melee_player_id = standings.melee_player_id
                  )
                ORDER BY melee_player_id
            ) s
            JOIN (
                SELECT melee_player_id,
                       ARRAY_AGG(DISTINCT event_id) AS event_ids
                FROM standings
                WHERE melee_player_id IS NOT NULL
                GROUP BY melee_player_id
            ) e ON e.melee_player_id = s.melee_player_id
        """)
        if not unmapped:
            return

        log.info(f"Assigning identities to {len(unmapped)} new melee player IDs...")

        for row in unmapped:
            pid   = row['melee_player_id']
            name  = row['player_name'].strip()
            evts  = set(row['event_ids'] or [])

            identity_id = None
            confidence  = 'manual'
            status      = 'confirmed'

            # ── Pass 1: match by stable melee account UUID (definitive) ──────
            # Check if this standing has a profile URL and we can resolve a UUID
            profile_url = db.fetchone(
                "SELECT melee_profile_url FROM standings WHERE melee_player_id=%s AND melee_profile_url IS NOT NULL LIMIT 1",
                (pid,)
            )
            if profile_url:
                import re as _re, time as _time
                try:
                    sess2 = _get_session()
                    html = sess2.get(profile_url['melee_profile_url'], timeout=15).text
                    m = _re.search(
                        r'userphotos/userprofiles/([a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12})',
                        html
                    )
                    if m:
                        uuid_val = m.group(1)
                        username = profile_url['melee_profile_url'].rstrip('/').split('/')[-1]
                        # Check if this UUID already maps to an existing identity
                        existing_uuid = db.fetchone(
                            "SELECT id FROM player_identities WHERE melee_account_id = %s::uuid",
                            (uuid_val,)
                        )
                        if existing_uuid:
                            identity_id = str(existing_uuid['id'])
                            confidence  = 'auto_high'
                            status      = 'confirmed'
                        else:
                            # New identity — create with UUID
                            new_id = db.fetchone(
                                """INSERT INTO player_identities (display_name, melee_account_id, melee_username)
                                   VALUES (%s, %s::uuid, %s) RETURNING id""",
                                (name, uuid_val, username)
                            )
                            identity_id = str(new_id['id'])
                            confidence  = 'auto_high'
                            status      = 'confirmed'
                        _time.sleep(0.2)
                except Exception as e:
                    log.debug(f"UUID fetch failed for {pid}: {e}")

            # ── Pass 2: fall back to name matching if no UUID found ───────────
            if not identity_id:
                existing = db.fetchall("""
                    SELECT pi.id, pi.display_name,
                           ARRAY_AGG(DISTINCT s.event_id) AS event_ids
                    FROM player_id_map m
                    JOIN player_identities pi ON pi.id = m.identity_id
                    JOIN standings s ON s.melee_player_id = m.melee_player_id
                    WHERE LOWER(TRIM(pi.display_name)) = LOWER(TRIM(%s))
                      AND m.status = 'confirmed'
                    GROUP BY pi.id, pi.display_name
                """, (name,))

                for ex in existing:
                    ex_evts = set(ex['event_ids'] or [])
                    if not (evts & ex_evts):
                        # No event overlap — safe to assume same person
                        identity_id = str(ex['id'])
                        confidence  = 'auto_high'
                        status      = 'confirmed'
                        break
                    else:
                        # Same name, overlapping events — could be two different players;
                        # flag both for manual review rather than silently merging
                        confidence = 'auto_low'
                        status     = 'review'

                if not identity_id:
                    new_id = db.fetchone(
                        "INSERT INTO player_identities (display_name) VALUES (%s) RETURNING id",
                        (name,)
                    )
                    identity_id = str(new_id['id'])
                    if confidence == 'auto_low':
                        for ex in existing:
                            db.execute(
                                "UPDATE player_id_map SET status='review' WHERE identity_id=%s",
                                (str(ex['id']),)
                            )

            db.execute("""
                INSERT INTO player_id_map (melee_player_id, identity_id, display_name, confidence, status)
                VALUES (%s, %s::uuid, %s, %s, %s)
                ON CONFLICT (melee_player_id) DO NOTHING
            """, (pid, identity_id, name, confidence, status))

        log.info("Player identity assignment complete.")
        # Opportunistically fetch melee account UUIDs for any identities that
        # have a profile URL in standings but no UUID yet
        _fetch_missing_uuids()
    except Exception as e:
        log.warning(f"Could not refresh player identities: {e}")

def _fetch_missing_uuids():
    """
    For identities that have a melee profile URL in standings but no account UUID yet,
    fetch the UUID from the profile image src.
    Runs after identity assignment so we only fetch for confirmed/new identities.
    Rate-limited to avoid hammering melee.gg.
    """
    import re as _re, time as _time
    try:
        rows = db.fetchall("""
            SELECT DISTINCT
                pi.id            AS identity_id,
                s.melee_profile_url
            FROM player_identities pi
            JOIN player_id_map m ON m.identity_id = pi.id
            JOIN standings s ON s.melee_player_id = m.melee_player_id
            WHERE pi.melee_account_id IS NULL
              AND s.melee_profile_url IS NOT NULL
              AND s.melee_profile_url != ''
            LIMIT 50
        """)
        if not rows:
            return
        log.info(f"Fetching melee UUIDs for {len(rows)} identities...")
        fetched = 0
        for row in rows:
            try:
                html = _get_session().get(row['melee_profile_url'], timeout=15).text
                m = _re.search(
                    r'userphotos/userprofiles/([a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12})',
                    html
                )
                if m:
                    uuid_val = m.group(1)
                    # Also grab username from URL
                    username = row['melee_profile_url'].rstrip('/').split('/')[-1]
                    db.execute("""
                        UPDATE player_identities
                        SET melee_account_id = %s::uuid,
                            melee_username   = %s,
                            updated_at       = NOW()
                        WHERE id = %s
                    """, (uuid_val, username, row['identity_id']))
                    fetched += 1
                _time.sleep(0.3)  # be polite
            except Exception as e:
                log.debug(f"UUID fetch failed for {row['melee_profile_url']}: {e}")
        log.info(f"Fetched {fetched} new melee account UUIDs.")
    except Exception as e:
        log.warning(f"_fetch_missing_uuids error: {e}")


def _resolve_uuids_from_decklists():
    """
    For each player identity without a melee_account_id, fetch
    https://melee.gg/Profile/Index/{display_name} and extract the UUID
    from the profile photo src:
      <img src="https://cdn.melee.gg/userphotos/userprofiles/{UUID}.jpg">

    Run manually: python -m scraper.melee --resolve-uuids
    """
    import re as _re, time as _time

    rows = db.fetchall("""
        SELECT id AS identity_id, display_name
        FROM player_identities
        WHERE melee_account_id IS NULL
          AND display_name IS NOT NULL
          AND display_name != ''
        ORDER BY updated_at DESC
        LIMIT 3000
    """)

    if not rows:
        log.info("No identities need UUID resolution.")
        return

    log.info(f"Resolving UUIDs for {len(rows)} identities via profile pages...")
    resolved = skipped = 0
    for row in rows:
        name = row['display_name']
        try:
            sess = _get_session()
            resp = sess.get(f"https://melee.gg/Profile/Index/{name}", timeout=15, allow_redirects=True)
            if resp.status_code != 200:
                log.info(f"  {name}: HTTP {resp.status_code}")
                skipped += 1
                _time.sleep(0.2)
                continue
            html = resp.text
            m = _re.search(
                r'userphotos/userprofiles/([a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12})',
                html
            )
            if not m:
                # Profile exists but uses default avatar — no UUID in page
                log.debug(f"  {name}: profile found but no UUID (default avatar)")
                skipped += 1
                _time.sleep(0.2)
                continue

            uuid_val = m.group(1)

            # Check if this UUID already belongs to a different identity (merge case)
            existing = db.fetchone(
                "SELECT id FROM player_identities WHERE melee_account_id = %s::uuid",
                (uuid_val,)
            )
            if existing and str(existing['id']) != str(row['identity_id']):
                log.info(f"  Merging {name} into existing identity {existing['id']}")
                db.execute(
                    "UPDATE player_id_map SET identity_id = %s WHERE identity_id = %s",
                    (existing['id'], row['identity_id'])
                )
                db.execute("DELETE FROM player_identities WHERE id = %s", (row['identity_id'],))
                resolved += 1
            else:
                db.execute("""
                    UPDATE player_identities
                    SET melee_account_id = %s::uuid,
                        melee_username   = %s,
                        updated_at       = NOW()
                    WHERE id = %s
                """, (uuid_val, name, row['identity_id']))
                log.info(f"  ✓ {name} → {uuid_val[:8]}...")
                resolved += 1

            _time.sleep(0.3)
        except Exception as e:
            log.debug(f"  Failed for {name}: {e}")
            _time.sleep(0.2)

    log.info(f"Resolved {resolved}/{len(rows)} UUIDs. ({skipped} skipped — no profile or default avatar)")


def _parse_deck_name(deck_name: str) -> tuple[Optional[str], Optional[str]]:
    """
    'Tobias Beckett, People are Predictable - Naval Intelligence HQ'
    -> ('Tobias Beckett, People are Predictable', 'Naval Intelligence HQ')
    """
    if not deck_name:
        return None, None
    idx = deck_name.rfind(" - ")
    if idx == -1:
        return deck_name.strip(), None
    return deck_name[:idx].strip(), deck_name[idx+3:].strip()


def _parse_record(record: str) -> tuple[int, int, int]:
    """'7-0-0' -> (7, 0, 0)  wins, losses, draws"""
    m = re.match(r"(\d+)-(\d+)-(\d+)", record or "")
    if m:
        return int(m.group(1)), int(m.group(2)), int(m.group(3))
    return 0, 0, 0


def _round_type(name: str) -> str:
    n = name.lower()
    if "final"    in n: return "Finals"
    if "semi"     in n: return "Semifinals"
    if "quarter"  in n: return "Quarterfinals"
    if "top"      in n: return "Top Cut"
    return "Swiss"


def _matches_table_exists(tbl: str = "matches") -> bool:
    try:
        db.fetchone(f"SELECT 1 FROM {tbl} LIMIT 1")
        return True
    except Exception:
        return False


# ── CLI ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Scrape SWU tournament data via melee.gg"
    )
    parser.add_argument("--set",          default="LAW",   help="Set code (default: LAW)")
    parser.add_argument("--limit",        type=int, default=0, help="Max events (0=all)")
    parser.add_argument("--days",         type=int, default=5, help="Only scrape events from the last N days (0=all)")
    parser.add_argument("--melee-id",     default=None,    help="Import single tournament by melee ID")
    parser.add_argument("--cards",         action="store_true", help="Also fetch full decklists")
    parser.add_argument("--refresh-views", action="store_true", help="Just refresh materialized views")
    parser.add_argument("--resolve-uuids", action="store_true", help="Resolve melee account UUIDs from decklist pages")
    parser.add_argument("--eternal",       action="store_true", help="Scrape Eternal format events (uses eternal_ tables)")
    parser.add_argument("--swu",           action="store_true", help="Use official SWU API instead of hub (preferred)")
    parser.add_argument("--swu-type",      type=int, action="append", dest="swu_types",
                        metavar="TYPE_ID",
                        help=f"SWU event type ID to fetch (default: {SWU_TYPE_PQ}=PQ). Repeatable.")
    args = parser.parse_args()

    if args.resolve_uuids:
        _resolve_uuids_from_decklists()
    elif args.refresh_views:
        log.info("Refreshing materialized views...")
        db.execute_autocommit("SELECT refresh_all_views()")
        db.execute_autocommit("SELECT refresh_eternal_views()")
        log.info("Done.")
    elif args.melee_id:
        import_tournament(args.melee_id, fetch_cards=args.cards, eternal=args.eternal)
        if args.eternal:
            db.execute_autocommit("SELECT refresh_eternal_views()")
        else:
            db.execute_autocommit("SELECT refresh_all_views()")
    elif args.swu:
        sync_from_swu(
            event_type_ids = args.swu_types or [SWU_TYPE_PQ],
            fetch_cards    = args.cards,
            since_days     = args.days,
            eternal        = args.eternal,
            limit          = args.limit,
        )
    else:
        sync_from_hub(
            set_code    = args.set,
            limit       = args.limit,
            fetch_cards = args.cards,
            since_days  = args.days,
            eternal     = args.eternal,
        )
