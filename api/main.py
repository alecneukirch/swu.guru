"""
api/main.py
~~~~~~~~~~~
FastAPI app — serves both the API and the static frontend.

Run with:
    uvicorn api.main:app --reload --host 0.0.0.0 --port 8000
"""

from __future__ import annotations


from pathlib import Path
from typing import Optional


from fastapi import FastAPI, HTTPException, Query, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

import db

app = FastAPI(title="SWU Cards", version="1.0.0")


# ── Meta / date-range filter helper ───────────────────────────────────────────
# When a meta_id is provided (e.g. "JTL-post-ban", "SOR") we translate it into
# a date range filter on events.date rather than a simple set_code match.
# This means a query for "JTL-post-ban" only includes events that took place
# between 2025-04-11 and 2025-07-11, not all JTL events.

def meta_date_filter(meta_id: Optional[str]) -> tuple[str, list]:
    """
    Returns (sql_fragment, params) for filtering events by meta era.
    sql_fragment is something like "AND e.date >= %s AND e.date < %s"
    Returns ('', []) if meta_id is None/empty.
    """
    if not meta_id:
        return "", []
    row = db.fetchone(
        "SELECT start_date, end_date FROM metas WHERE id = %s",
        (meta_id,)
    )
    if not row:
        # Fall back to set_code match if meta not found
        # (handles the case where metas haven't been synced yet)
        set_code = meta_id.split("-")[0]  # "JTL-post-ban" -> "JTL"
        return "AND e.set_code = %s", [set_code]

    parts, params = [], []
    if row.get("start_date"):
        parts.append("e.date >= %s")
        params.append(row["start_date"])
    if row.get("end_date"):
        parts.append("e.date < %s")
        params.append(row["end_date"])

    if not parts:
        return "", []
    return "AND " + " AND ".join(parts), params

def _tnames(format: str) -> dict:
    """Return DB table/view names for the given format ('standard' or 'eternal')."""
    eternal = format == "eternal"
    p = "eternal_" if eternal else ""
    return {
        "events":               f"{p}events",
        "standings":            f"{p}standings",
        "decklist_cards":       f"{p}decklist_cards",
        "matches":              f"{p}matches",
        "mv_leader_stats":      "mv_eternal_leader_stats"      if eternal else "mv_leader_stats",
        "mv_card_leader_stats": "mv_eternal_card_leader_stats" if eternal else "mv_card_leader_stats",
        "mv_card_copy_matrix":  "mv_eternal_card_copy_matrix"  if eternal else "mv_card_copy_matrix",
    }

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

FRONTEND_DIR = Path(__file__).parent.parent / "frontend"

# ── Static frontend ────────────────────────────────────────────────────────
app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(str(FRONTEND_DIR / "index.html"))

@app.get("/favicon.svg", include_in_schema=False)
def favicon_svg():
    return FileResponse(str(FRONTEND_DIR / "favicon.svg"), media_type="image/svg+xml")

@app.get("/favicon.ico", include_in_schema=False)
def favicon_ico():
    return FileResponse(str(FRONTEND_DIR / "favicon.svg"), media_type="image/svg+xml")


# =============================================================================
#  SUMMARY
# =============================================================================

@app.get("/api/summary")
def summary(format: str = Query("standard"), meta_id: Optional[str] = Query(None)):
    t = _tnames(format)
    date_sql, date_params = meta_date_filter(meta_id)

    if date_sql:
        row = db.fetchone(f"""
            SELECT
                COUNT(DISTINCT e.id)                                    AS total_events,
                COUNT(DISTINCT s.id)                                    AS total_standings,
                COUNT(DISTINCT s.leader) FILTER (WHERE s.leader IS NOT NULL)
                                                                        AS unique_leaders,
                COUNT(DISTINCT s.id) FILTER (WHERE s.has_decklist)     AS decklists_with_cards,
                MAX(e.date)                                             AS last_event_date,
                (SELECT COUNT(*) FROM {t['matches']} m
                 JOIN {t['events']} e ON e.id = m.event_id
                 WHERE 1=1 {date_sql})                                  AS total_matches
            FROM {t['events']} e
            LEFT JOIN {t['standings']} s ON s.event_id = e.id
            WHERE 1=1 {date_sql}
        """, date_params + date_params)
    else:
        row = db.fetchone(f"""
            SELECT
                (SELECT COUNT(*) FROM {t['events']})                   AS total_events,
                (SELECT COUNT(*) FROM {t['standings']})                AS total_standings,
                (SELECT COUNT(DISTINCT leader) FROM {t['standings']} WHERE leader IS NOT NULL)
                                                                       AS unique_leaders,
                (SELECT COUNT(*) FROM {t['standings']} WHERE has_decklist)
                                                                       AS decklists_with_cards,
                (SELECT MAX(date) FROM {t['events']})                  AS last_event_date,
                (SELECT COUNT(*) FROM {t['matches']})                  AS total_matches
        """)
    return row or {}


# =============================================================================
#  LEADERS  —  Home page grid + meta overview
# =============================================================================

@app.get("/api/leaders")
def leaders(
    meta_id:   Optional[str] = Query(None),
    min_decks: int            = Query(5),
    or_has_t8: bool           = Query(False, description="Also include leaders with any top-8 finish regardless of deck count"),
    format:    str            = Query("standard"),
):
    """
    Returns aggregated leader stats (summed across all bases).
    Includes conversion vs expected (T8 rate / meta-average T8 rate).
    meta_id filters by date range derived from the metas table
    (e.g. 'JTL-post-ban', 'SOR', 'eternal-post-ban').
    """
    t = _tnames(format)
    date_sql, date_params = meta_date_filter(meta_id)

    t8_having_raw = (
        "OR COUNT(DISTINCT s.id) FILTER "
        "(WHERE s.placement <= GREATEST(CEIL(e.player_count::numeric * 0.08)::INT, 1)) >= 1"
    ) if or_has_t8 else ""
    t8_having_mv = "OR SUM(top8_count) >= 1" if or_has_t8 else ""

    # When meta filtering is active we need to recompute from raw standings
    # rather than the materialized view (which doesn't have event dates).
    if date_sql:
        rows = db.fetchall(f"""
            SELECT
                s.leader                                                AS leader,
                COUNT(DISTINCT s.id)::INT                               AS total_decks,
                COUNT(DISTINCT s.id) FILTER (
                    WHERE s.placement <= GREATEST(CEIL(e.player_count::numeric * 0.08)::INT, 1)
                )::INT                                                  AS top8_count,
                COUNT(DISTINCT s.id) FILTER (
                    WHERE s.placement = 1
                )::INT                                                  AS wins
            FROM {t['standings']} s
            JOIN {t['events']} e ON e.id = s.event_id
            WHERE s.leader IS NOT NULL
              AND s.placement IS NOT NULL
              {date_sql}
            GROUP BY s.leader
            HAVING COUNT(DISTINCT s.id) >= %s {t8_having_raw}
            ORDER BY COUNT(DISTINCT s.id) DESC
        """, date_params + [min_decks])
    else:
        rows = db.fetchall(f"""
            SELECT
                v.leader,
                v.total_decks,
                v.top8_count,
                v.wins,
                ROUND(ms.match_wins::numeric / NULLIF(ms.match_games, 0), 4) AS match_win_rate,
                COALESCE(ms.match_games, 0)::INT AS match_games
            FROM (
                SELECT leader,
                       SUM(total_decks)::INT AS total_decks,
                       SUM(top8_count)::INT  AS top8_count,
                       SUM(wins)::INT        AS wins
                FROM {t['mv_leader_stats']}
                GROUP BY leader
                HAVING SUM(total_decks) >= %s {t8_having_mv}
            ) v
            LEFT JOIN (
                SELECT leader,
                       SUM(mw)::INT AS match_wins,
                       SUM(mg)::INT AS match_games
                FROM (
                    SELECT p1_leader AS leader,
                           COUNT(*) FILTER (WHERE winner = 'p1') AS mw,
                           COUNT(*) FILTER (WHERE winner IN ('p1','p2')) AS mg
                    FROM {t['matches']} WHERE p1_leader IS NOT NULL AND winner IS NOT NULL
                    GROUP BY p1_leader
                    UNION ALL
                    SELECT p2_leader,
                           COUNT(*) FILTER (WHERE winner = 'p2'),
                           COUNT(*) FILTER (WHERE winner IN ('p1','p2'))
                    FROM {t['matches']} WHERE p2_leader IS NOT NULL AND winner IS NOT NULL
                    GROUP BY p2_leader
                ) t2 GROUP BY leader
            ) ms ON ms.leader = v.leader
            ORDER BY v.total_decks DESC
        """, [min_decks])

    if not rows:
        return []

    total_all_decks = sum(r["total_decks"] for r in rows)
    total_all_t8s   = sum(r["top8_count"]  for r in rows)
    meta_t8_rate    = (total_all_t8s / total_all_decks) if total_all_decks else 0

    # Percentile conversions — always from raw standings so both paths get them
    pct_rows = db.fetchall(f"""
        SELECT
            s.leader,
            ROUND(COUNT(DISTINCT s.id) FILTER (
                WHERE s.placement <= GREATEST(CEIL(e.player_count::numeric * 0.50)::INT, 1)
            )::numeric / NULLIF(COUNT(DISTINCT s.id), 0) / 0.50, 3) AS t50_conv,
            ROUND(COUNT(DISTINCT s.id) FILTER (
                WHERE s.placement <= GREATEST(CEIL(e.player_count::numeric * 0.25)::INT, 1)
            )::numeric / NULLIF(COUNT(DISTINCT s.id), 0) / 0.25, 3) AS t25_conv,
            ROUND(COUNT(DISTINCT s.id) FILTER (
                WHERE s.placement <= GREATEST(CEIL(e.player_count::numeric * 0.10)::INT, 1)
            )::numeric / NULLIF(COUNT(DISTINCT s.id), 0) / 0.10, 3) AS t10_conv,
            ROUND(COUNT(DISTINCT s.id) FILTER (
                WHERE s.placement <= GREATEST(CEIL(e.player_count::numeric * 0.01)::INT, 1)
            )::numeric / NULLIF(COUNT(DISTINCT s.id), 0) / 0.01, 3) AS t1_conv
        FROM {t['standings']} s
        JOIN {t['events']} e ON e.id = s.event_id
        WHERE s.leader IS NOT NULL
          AND s.placement IS NOT NULL
          {date_sql}
        GROUP BY s.leader
        HAVING COUNT(DISTINCT s.id) >= %s {t8_having_raw}
    """, date_params + [min_decks])
    pct_by_leader = {r['leader']: r for r in pct_rows}

    result = []
    for r in rows:
        t8_rate    = r["top8_count"] / r["total_decks"] if r["total_decks"] else 0
        conversion = round(t8_rate / meta_t8_rate, 3) if meta_t8_rate else None
        pct = pct_by_leader.get(r['leader'], {})
        result.append({
            **r,
            "meta_share":  round(r["total_decks"] / total_all_decks, 4) if total_all_decks else 0,
            "t8_rate":     round(t8_rate, 4),
            "conversion":  conversion,
            "t50_conv":    float(pct['t50_conv']) if pct.get('t50_conv') is not None else None,
            "t25_conv":    float(pct['t25_conv']) if pct.get('t25_conv') is not None else None,
            "t10_conv":    float(pct['t10_conv']) if pct.get('t10_conv') is not None else None,
            "t1_conv":     float(pct['t1_conv'])  if pct.get('t1_conv')  is not None else None,
        })

    result.sort(key=lambda x: x["total_decks"], reverse=True)
    return result


# =============================================================================
#  LEADER+BASE COMBOS  —  Home page breakdown by leader+base group
# =============================================================================

@app.get("/api/leaders/by-base")
def leaders_by_base(
    meta_id:   Optional[str] = Query(None),
    min_decks: int            = Query(3),
    or_has_t8: bool           = Query(False),
    format:    str            = Query("standard"),
):
    """
    Returns stats for each leader+base-group combo.
    Base groups use aspect/ability classification: plain, splash, force, or rare.
    """
    t = _tnames(format)
    date_sql, date_params = meta_date_filter(meta_id)

    t8_having_raw = (
        "OR COUNT(DISTINCT s.id) FILTER "
        "(WHERE s.placement <= GREATEST(CEIL(e.player_count::numeric * 0.08)::INT, 1)) >= 1"
    ) if or_has_t8 else ""
    t8_having_mv = "OR SUM(top8_count) >= 1" if or_has_t8 else ""

    if date_sql:
        rows = db.fetchall(f"""
            SELECT s.leader, s.base,
                   COUNT(DISTINCT s.id)::INT AS total_decks,
                   COUNT(DISTINCT s.id) FILTER (
                       WHERE s.placement <= GREATEST(CEIL(e.player_count::numeric * 0.08)::INT, 1)
                   )::INT AS top8_count,
                   COUNT(DISTINCT s.id) FILTER (WHERE s.placement = 1)::INT AS wins,
                   COALESCE(MAX(ms.match_wins),  0)::INT AS match_wins,
                   COALESCE(MAX(ms.match_games), 0)::INT AS match_games
            FROM {t['standings']} s
            JOIN {t['events']} e ON e.id = s.event_id
            LEFT JOIN (
                SELECT leader, base,
                       SUM(mw)::INT AS match_wins,
                       SUM(mg)::INT AS match_games
                FROM (
                    SELECT p1_leader AS leader, p1_base AS base,
                           COUNT(*) FILTER (WHERE winner = 'p1') AS mw,
                           COUNT(*) FILTER (WHERE winner IN ('p1','p2')) AS mg
                    FROM {t['matches']}
                    WHERE p1_leader IS NOT NULL AND p1_base IS NOT NULL AND winner IS NOT NULL
                    GROUP BY p1_leader, p1_base
                    UNION ALL
                    SELECT p2_leader, p2_base,
                           COUNT(*) FILTER (WHERE winner = 'p2'),
                           COUNT(*) FILTER (WHERE winner IN ('p1','p2'))
                    FROM {t['matches']}
                    WHERE p2_leader IS NOT NULL AND p2_base IS NOT NULL AND winner IS NOT NULL
                    GROUP BY p2_leader, p2_base
                ) t2 GROUP BY leader, base
            ) ms ON ms.leader = s.leader AND ms.base = s.base
            WHERE s.leader IS NOT NULL AND s.base IS NOT NULL
              AND s.placement IS NOT NULL
              {date_sql}
            GROUP BY s.leader, s.base
            HAVING COUNT(DISTINCT s.id) >= %s {t8_having_raw}
        """, date_params + [min_decks])
    else:
        rows = db.fetchall(f"""
            SELECT v.leader, v.base,
                   v.total_decks, v.top8_count, v.wins,
                   COALESCE(ms.match_wins,  0)::INT AS match_wins,
                   COALESCE(ms.match_games, 0)::INT AS match_games
            FROM (
                SELECT leader, base,
                       SUM(total_decks)::INT AS total_decks,
                       SUM(top8_count)::INT  AS top8_count,
                       SUM(wins)::INT        AS wins
                FROM {t['mv_leader_stats']}
                WHERE leader IS NOT NULL AND base IS NOT NULL
                GROUP BY leader, base
                HAVING SUM(total_decks) >= %s {t8_having_mv}
            ) v
            LEFT JOIN (
                SELECT leader, base,
                       SUM(mw)::INT AS match_wins,
                       SUM(mg)::INT AS match_games
                FROM (
                    SELECT p1_leader AS leader, p1_base AS base,
                           COUNT(*) FILTER (WHERE winner = 'p1') AS mw,
                           COUNT(*) FILTER (WHERE winner IN ('p1','p2')) AS mg
                    FROM {t['matches']}
                    WHERE p1_leader IS NOT NULL AND p1_base IS NOT NULL AND winner IS NOT NULL
                    GROUP BY p1_leader, p1_base
                    UNION ALL
                    SELECT p2_leader, p2_base,
                           COUNT(*) FILTER (WHERE winner = 'p2'),
                           COUNT(*) FILTER (WHERE winner IN ('p1','p2'))
                    FROM {t['matches']}
                    WHERE p2_leader IS NOT NULL AND p2_base IS NOT NULL AND winner IS NOT NULL
                    GROUP BY p2_leader, p2_base
                ) t2 GROUP BY leader, base
            ) ms ON ms.leader = v.leader AND ms.base = v.base
        """, [min_decks])

    if not rows:
        return []

    # Look up base group info for all bases seen
    all_bases = list({r['base'] for r in rows if r['base']})
    if all_bases:
        ph = ','.join(['%s'] * len(all_bases))
        base_cards = db.fetchall(f"""
            SELECT DISTINCT ON (name) name,
                   COALESCE(aspects[1], 'none') AS aspect,
                   rarity, card_text
            FROM cards
            WHERE is_base = true AND variant_type = 'Standard'
              AND name IN ({ph})
            ORDER BY name, set_code DESC
        """, all_bases)
        base_meta = {r['name']: r for r in base_cards}
    else:
        base_meta = {}

    # Compute meta-wide T8 rate for conversion denominator
    total_all_decks = sum(r['total_decks'] for r in rows)
    total_all_t8s   = sum(r['top8_count']  for r in rows)
    meta_t8_rate    = (total_all_t8s / total_all_decks) if total_all_decks else 0

    # Aggregate by leader + base group
    groups: dict = {}
    for r in rows:
        meta    = base_meta.get(r['base'], {})
        aspect  = meta.get('aspect', 'none') or 'none'
        rarity  = meta.get('rarity', 'Common') or 'Common'
        ability = _base_ability_type(
            meta.get('card_text') or '',
            meta.get('deploy_box') or '',
            meta.get('epic_action') or '',
            r['base']
        )
        grp_key = _base_group_key(aspect, ability, rarity, r['base'])
        grp_lbl = _base_group_label(aspect, ability, rarity, r['base'])

        combo_key = f"{r['leader']}|||{grp_key}"
        if combo_key not in groups:
            groups[combo_key] = {
                'leader':           r['leader'],
                'base_group':       grp_lbl,
                'base_key':         grp_key,
                'aspect':           aspect,
                'ability':          ability,
                'rarity':           rarity,
                'total_decks':      0,
                'top8_count':       0,
                'wins':             0,
                '_match_wins':      0,
                '_match_games':     0,
                'bases':            [],
            }
        groups[combo_key]['total_decks']  += r['total_decks']
        groups[combo_key]['top8_count']   += r['top8_count']
        groups[combo_key]['wins']         += r['wins']
        groups[combo_key]['_match_wins']  += r.get('match_wins')  or 0
        groups[combo_key]['_match_games'] += r.get('match_games') or 0
        if r['base'] not in groups[combo_key]['bases']:
            groups[combo_key]['bases'].append(r['base'])

    result = []
    for g in groups.values():
        t8_rate    = g['top8_count'] / g['total_decks'] if g['total_decks'] else 0
        conversion = round(t8_rate / meta_t8_rate, 3) if meta_t8_rate else None
        result.append({
            **{k: v for k, v in g.items() if not k.startswith('_')},
            'meta_share':     round(g['total_decks'] / total_all_decks, 4) if total_all_decks else 0,
            't8_rate':        round(t8_rate, 4),
            'conversion':     conversion,
            'match_win_rate': round(g['_match_wins'] / g['_match_games'], 4) if g['_match_games'] else None,
            'match_games':    g['_match_games'],
        })

    result.sort(key=lambda x: x['total_decks'], reverse=True)
    return result


# =============================================================================
#  LEADER DETAIL
# =============================================================================

@app.get("/api/leader/{leader}/stats")
def leader_stats(
    leader:     str,
    meta_id:    Optional[str] = Query(None),
    base_group: Optional[str] = Query(None, description="Comma-separated base names to filter by"),
    format:     str            = Query("standard"),
):
    """Hero stats for the leader page header. Supports base group filtering."""
    t = _tnames(format)
    date_sql, date_params = meta_date_filter(meta_id)
    base_names = [b.strip() for b in base_group.split(',') if b.strip()] if base_group else []

    if base_names:
        base_filter = "AND s.base = ANY(%s::text[])"
        base_params = [base_names]
    else:
        base_filter = ""
        base_params = []

    # Always use live query when base filter is active (matview doesn't store base)
    if date_sql or base_names:
        row = db.fetchone(f"""
            SELECT
                s.leader,
                COUNT(DISTINCT s.id)::INT AS total_decks,
                COUNT(DISTINCT s.id) FILTER (
                    WHERE s.placement <= GREATEST(CEIL(e.player_count::numeric * 0.08)::INT, 1)
                )::INT AS top8_count,
                COUNT(DISTINCT s.id) FILTER (
                    WHERE s.placement <= GREATEST(CEIL(e.player_count::numeric * 0.16)::INT, 1)
                )::INT AS top16_count,
                COUNT(DISTINCT s.id) FILTER (WHERE s.placement = 1)::INT AS wins
            FROM {t['standings']} s
            JOIN {t['events']} e ON e.id = s.event_id
            WHERE s.leader = %s AND s.placement IS NOT NULL
              {date_sql} {base_filter}
            GROUP BY s.leader
        """, [leader] + date_params + base_params)

        # Meta rate: all leaders, same date range but NOT base-filtered (conversion is vs global meta)
        meta = db.fetchone(f"""
            SELECT COUNT(DISTINCT s.id) AS all_decks,
                   COUNT(DISTINCT s.id) FILTER (
                       WHERE s.placement <= GREATEST(CEIL(e.player_count::numeric * 0.08)::INT, 1)
                   ) AS all_t8s
            FROM {t['standings']} s
            JOIN {t['events']} e ON e.id = s.event_id
            WHERE s.leader IS NOT NULL AND s.placement IS NOT NULL
              {date_sql}
        """, date_params)
    else:
        row = db.fetchone(f"""
            SELECT leader,
                   SUM(total_decks)::INT  AS total_decks,
                   SUM(top8_count)::INT   AS top8_count,
                   SUM(top16_count)::INT  AS top16_count,
                   SUM(wins)::INT         AS wins
            FROM {t['mv_leader_stats']}
            WHERE leader = %s
            GROUP BY leader
        """, [leader])

        meta = db.fetchone(f"""
            SELECT SUM(total_decks) AS all_decks, SUM(top8_count) AS all_t8s
            FROM {t['mv_leader_stats']}
        """)

    if not row:
        raise HTTPException(404, f"Leader '{leader}' not found")

    meta_t8_rate = (meta["all_t8s"] / meta["all_decks"]) if meta and meta["all_decks"] else 0
    t8_rate      = row["top8_count"] / row["total_decks"] if row["total_decks"] else 0
    conversion   = round(t8_rate / meta_t8_rate, 3) if meta_t8_rate else None

    pct = db.fetchone(f"""
        SELECT
            ROUND(COUNT(DISTINCT s.id) FILTER (
                WHERE s.placement <= GREATEST(CEIL(e.player_count::numeric * 0.50)::INT, 1)
            )::numeric / NULLIF(COUNT(DISTINCT s.id), 0) / 0.50, 3) AS t50_conv,
            ROUND(COUNT(DISTINCT s.id) FILTER (
                WHERE s.placement <= GREATEST(CEIL(e.player_count::numeric * 0.25)::INT, 1)
            )::numeric / NULLIF(COUNT(DISTINCT s.id), 0) / 0.25, 3) AS t25_conv,
            ROUND(COUNT(DISTINCT s.id) FILTER (
                WHERE s.placement <= GREATEST(CEIL(e.player_count::numeric * 0.10)::INT, 1)
            )::numeric / NULLIF(COUNT(DISTINCT s.id), 0) / 0.10, 3) AS t10_conv,
            ROUND(COUNT(DISTINCT s.id) FILTER (
                WHERE s.placement <= GREATEST(CEIL(e.player_count::numeric * 0.01)::INT, 1)
            )::numeric / NULLIF(COUNT(DISTINCT s.id), 0) / 0.01, 3) AS t1_conv
        FROM {t['standings']} s
        JOIN {t['events']} e ON e.id = s.event_id
        WHERE s.leader = %s
          AND s.placement IS NOT NULL
          {date_sql} {base_filter}
    """, [leader] + date_params + base_params)

    return {
        **row,
        "t8_rate":    round(t8_rate, 4),
        "conversion": conversion,
        "t50_conv":   float(pct['t50_conv']) if pct and pct.get('t50_conv') is not None else None,
        "t25_conv":   float(pct['t25_conv']) if pct and pct.get('t25_conv') is not None else None,
        "t10_conv":   float(pct['t10_conv']) if pct and pct.get('t10_conv') is not None else None,
        "t1_conv":    float(pct['t1_conv'])  if pct and pct.get('t1_conv')  is not None else None,
    }


# Cache for base_reference lookups to avoid repeated DB hits
_base_ref_cache: dict = {}

def _load_base_ref():
    """Load the base_reference table into memory on first use."""
    global _base_ref_cache
    if _base_ref_cache:
        return
    try:
        rows = db.fetchall("SELECT name, aspect, ability, label, rarity FROM base_reference")
        _base_ref_cache = {r["name"].lower(): r for r in rows}
    except Exception:
        _base_ref_cache = {}


def _base_ref(base_name: str) -> Optional[dict]:
    """Look up a base by name, case-insensitive."""
    _load_base_ref()
    return _base_ref_cache.get((base_name or '').lower().strip())


def _base_ability_type(card_text: str = '', deploy_box: str = None,
                       epic_action: str = None, base_name: str = '') -> str:
    """
    Classify a base's ability type: 'force', 'splash', or 'plain'.
    Uses base_reference table first (most reliable), then falls back to
    text-based keyword scanning.
    """
    ref = _base_ref(base_name)
    if ref:
        return ref["ability"]

    combined = ' '.join(filter(None, [card_text, deploy_box, epic_action])).lower()
    if not combined.strip():
        return 'plain'
    if 'force' in combined:
        return 'force'
    if any(kw in combined for kw in ['resource', 'aspect', 'penalty', 'deploy', 'pay', 'produce']):
        return 'splash'
    return 'splash'



def _base_group_label(aspect: str, ability: str, rarity: str, base_name: str = '') -> str:
    """Human-readable label for a base group."""
    if rarity != 'Common':
        return base_name or f"Rare ({aspect})"
    aspect_name = aspect if aspect and aspect != 'none' else 'No Aspect'
    # Plain is the default/expected for common bases — no need to label it
    if ability == 'plain':
        return aspect_name
    ability_tag = {'force': 'Force', 'splash': 'Splash'}.get(ability, ability.title())
    return f"{aspect_name} — {ability_tag}"


def _base_group_key(aspect: str, ability: str, rarity: str, base_name: str = '') -> str:
    """Stable key for a base group used as the filter value."""
    if rarity != 'Common':
        # Rare bases each get their own group keyed by name
        return f"rare__{base_name.lower().replace(' ', '_')}"
    a = (aspect or 'none').lower()
    return f"{a}__{ability}"


_bases_cache: dict = {}  # key: (leader, meta_id) → list
_leader_image_cache: dict = {}  # key: leader_name → row

@app.get("/api/leader/{leader}/bases")
def leader_bases(
    leader:  str,
    meta_id: Optional[str] = Query(None),
    format:  str            = Query("standard"),
):
    """
    Returns base groups (not individual bases) for the filter dropdown.
    Each group covers all bases that share the same aspect color, rarity tier,
    and whether they have a special ability, since these are functionally
    interchangeable from a deckbuilding perspective.
    """
    cache_key = (leader, meta_id or '', format)
    if cache_key in _bases_cache:
        return _bases_cache[cache_key]

    t = _tnames(format)
    date_sql, date_params = meta_date_filter(meta_id)

    # Get individual base counts from standings
    if date_sql:
        rows = db.fetchall(f"""
            SELECT s.base,
                   COUNT(DISTINCT s.id)::INT AS decks,
                   COUNT(DISTINCT s.id) FILTER (
                       WHERE s.placement <= GREATEST(CEIL(e.player_count::numeric * 0.08)::INT, 1)
                   )::INT AS top8_count
            FROM {t['standings']} s
            JOIN {t['events']} e ON e.id = s.event_id
            WHERE s.leader = %s AND s.base IS NOT NULL
              AND s.placement IS NOT NULL
              {date_sql}
            GROUP BY s.base
        """, [leader] + date_params)
    else:
        rows = db.fetchall(f"""
            SELECT base,
                   SUM(total_decks)::INT AS decks,
                   SUM(top8_count)::INT  AS top8_count
            FROM {t['mv_leader_stats']}
            WHERE leader = %s AND base IS NOT NULL
            GROUP BY base
        """, [leader])

    if not rows:
        return []

    # Look up each base's aspect/rarity/ability from the cards table
    base_names = [r['base'] for r in rows if r.get('base')]
    if not base_names:
        return []

    # Look up card_text for ability classification
    ph = ','.join(['%s'] * len(base_names))
    card_info_full = db.fetchall(f"""
        SELECT DISTINCT ON (name) name,
               COALESCE(aspects[1], 'none') AS aspect,
               rarity, card_text, deploy_box, epic_action
        FROM cards
        WHERE is_base = true
          AND variant_type = 'Standard'
          AND name IN ({ph})
        ORDER BY name, set_code DESC
    """, base_names)

    # Build lookup: base_name -> {aspect, rarity, card_text, ...}
    base_meta = {r['name']: r for r in card_info_full}

    # Aggregate into groups
    groups: dict = {}
    for r in rows:
        base_name = r.get('base')
        if not base_name:
            continue
        meta      = base_meta.get(base_name, {})
        aspect    = meta.get('aspect', 'none') or 'none'
        rarity    = meta.get('rarity', 'Common') or 'Common'
        ability   = _base_ability_type(
            meta.get('card_text') or '',
            meta.get('deploy_box') or '',
            meta.get('epic_action') or '',
            base_name
        )

        key   = _base_group_key(aspect, ability, rarity, base_name)
        label = _base_group_label(aspect, ability, rarity, base_name)

        if key not in groups:
            groups[key] = {
                'group_key': key,
                'label':     label,
                'aspect':    aspect,
                'rarity':    rarity,
                'ability':   ability,
                'decks':       0,
                'top8_count':  0,
                'bases':       [],
            }
        groups[key]['decks']      += r['decks']
        groups[key]['top8_count'] += r['top8_count']
        groups[key]['bases'].append(base_name)

    result = sorted(groups.values(), key=lambda g: -g['decks'])
    for g in result:
        total = g['decks']
        g['t8_rate'] = round(g['top8_count'] / total, 4) if total else 0
    _bases_cache[cache_key] = result
    return result


@app.get("/api/leader/{leader}/cards")
def leader_cards(
    leader:       str,
    base_group:   Optional[str]  = Query(None, description="Comma-separated list of base names from a base group"),
    meta_id:      Optional[str]  = Query(None),
    is_sideboard: Optional[bool] = Query(None),
    min_decks:    int             = Query(5),
    top8_only:    bool            = Query(False),
    format:       str             = Query("standard"),
):
    """
    Card list for a leader with inclusion %, avg copies, T8 conversion.
    base_group is a comma-separated list of base names (all belonging to the same
    group — same aspect/ability/rarity) so they can be filtered together.
    top8_only restricts the deck universe to only decks that made top 8.
    """
    t = _tnames(format)
    date_sql, date_params = meta_date_filter(meta_id)
    base_names  = [b.strip() for b in base_group.split(',') if b.strip()] if base_group else []
    sb_filter   = "" if is_sideboard is None else ("AND dc.is_sideboard = true" if is_sideboard else "AND dc.is_sideboard = false")
    base_filter = "AND s.base = ANY(%s::text[])" if base_names else ""
    base_params = [base_names] if base_names else []
    t8_filter   = "AND s.placement <= GREATEST(CEIL(e.player_count::numeric * 0.08)::INT, 1)" if top8_only else ""

    if date_sql or base_names or top8_only:
        rows = db.fetchall(f"""
            WITH leader_totals AS (
                SELECT
                    COUNT(DISTINCT s.id)::INT                                       AS leader_total_decks,
                    COUNT(DISTINCT s.id) FILTER (
                        WHERE s.placement <= GREATEST(CEIL(e.player_count::numeric * 0.08)::INT, 1)
                    )::INT                                                           AS leader_total_t8s
                FROM {t['standings']} s
                JOIN {t['events']} e ON e.id = s.event_id
                WHERE s.leader = %s
                  AND s.placement IS NOT NULL
                  {date_sql} {base_filter} {t8_filter}
            ),
            card_stats AS (
                SELECT
                    dc.card_name,
                    dc.is_sideboard,
                    COUNT(DISTINCT s.id)::INT                                       AS deck_count,
                    ROUND(SUM(dc.quantity)::numeric / NULLIF(COUNT(DISTINCT s.id),0)::numeric, 2) AS avg_copies,
                    COUNT(DISTINCT s.id) FILTER (
                        WHERE s.placement <= GREATEST(CEIL(e.player_count::numeric * 0.08)::INT, 1)
                    )::INT                                                           AS t8_count
                FROM {t['decklist_cards']} dc
                JOIN {t['standings']} s ON s.id = dc.standing_id
                JOIN {t['events']} e ON e.id = s.event_id
                WHERE s.leader = %s
                  AND s.placement IS NOT NULL
                  {date_sql} {base_filter} {sb_filter} {t8_filter}
                GROUP BY dc.card_name, dc.is_sideboard
                HAVING COUNT(DISTINCT s.id) >= %s
            )
            SELECT
                cs.card_name,
                cs.is_sideboard,
                cs.deck_count,
                cs.avg_copies,
                cs.t8_count,
                lt.leader_total_decks,
                lt.leader_total_t8s,
                ROUND(cs.deck_count::numeric / NULLIF(lt.leader_total_decks,0)::numeric, 4) AS inclusion_rate,
                ROUND(cs.t8_count::numeric   / NULLIF(cs.deck_count,0)::numeric, 4)         AS card_t8_rate,
                ROUND(lt.leader_total_t8s::numeric / NULLIF(lt.leader_total_decks,0)::numeric, 4) AS baseline_t8_rate,
                ROUND(
                    (cs.t8_count::numeric / NULLIF(cs.deck_count,0)::numeric)
                    / NULLIF(lt.leader_total_t8s::numeric / NULLIF(lt.leader_total_decks,0)::numeric, 0),
                4) AS conversion,
                COALESCE(c.type, 'Unit') AS card_type,
                COALESCE(c.cost, 0)      AS cost,
                c.arena                  AS arena
            FROM card_stats cs
            CROSS JOIN leader_totals lt
            LEFT JOIN (
                SELECT DISTINCT ON (name, COALESCE(subtitle,''))
                    name, subtitle, type, cost, arena
                FROM cards
                WHERE variant_type = 'Standard'
                  AND is_leader = false
                  AND is_base   = false
                ORDER BY name, COALESCE(subtitle,''), set_code DESC
            ) c ON c.name = SPLIT_PART(cs.card_name, ' | ', 1)
                AND (
                    SPLIT_PART(cs.card_name, ' | ', 2) = ''
                    OR c.subtitle = SPLIT_PART(cs.card_name, ' | ', 2)
                )
            WHERE c.type IS NOT NULL
            ORDER BY inclusion_rate DESC, deck_count DESC
        """, [leader] + date_params + base_params + [leader] + date_params + base_params + [min_decks])

    else:
        # Fast path: serve from materialized view
        params: list = [leader, min_decks]
        filters = "AND leader = %s AND deck_count >= %s"
        if is_sideboard is not None:
            filters += " AND is_sideboard = %s"
            params.append(is_sideboard)
        rows = db.fetchall(f"""
            SELECT
                m.card_name, m.is_sideboard, m.deck_count, m.avg_copies,
                m.t8_count, m.leader_total_decks, m.leader_total_t8s,
                m.inclusion_rate, m.card_t8_rate, m.baseline_t8_rate, m.conversion,
                COALESCE(c.type, 'Unit') AS card_type,
                COALESCE(c.cost, 0)      AS cost,
                c.arena                  AS arena
            FROM {t['mv_card_leader_stats']} m
            LEFT JOIN (
                SELECT DISTINCT ON (name, COALESCE(subtitle,''))
                    name, subtitle, type, cost, arena
                FROM cards
                WHERE variant_type = 'Standard'
                  AND is_leader = false
                  AND is_base   = false
                ORDER BY name, COALESCE(subtitle,''), set_code DESC
            ) c ON c.name = SPLIT_PART(m.card_name, ' | ', 1)
                AND (
                    SPLIT_PART(m.card_name, ' | ', 2) = ''
                    OR c.subtitle = SPLIT_PART(m.card_name, ' | ', 2)
                )
            WHERE 1=1 {filters}
              AND c.type IS NOT NULL
            ORDER BY m.inclusion_rate DESC, m.deck_count DESC
        """, params)

    return rows


# =============================================================================
#  CARD DETAIL  —  Powers the card modal
# =============================================================================

@app.get("/api/card/{card_name}/by-leader")
def card_by_leader(
    card_name: str,
    set_code:  Optional[str] = Query(None),
    percentile: float        = Query(0.08, description="Top-cut fraction, e.g. 0.08 = top 8%"),
):
    """
    For a single card: performance broken down by each leader.
    Used for the 'Conversion by Legend' section in the card modal.
    """
    params: list = [card_name]
    set_filter = ""
    if set_code:
        set_filter = "AND set_code = %s"
        params.append(set_code)

    rows = db.fetchall(f"""
        SELECT
            m.leader,
            SUM(m.deck_count)           AS deck_count,
            SUM(m.t8_count)             AS t8_count,
            SUM(m.leader_total_decks)   AS leader_total_decks,
            SUM(m.leader_total_t8s)     AS leader_total_t8s,
            ROUND(SUM(m.deck_count)::numeric
                / NULLIF(SUM(m.leader_total_decks), 0), 4) AS inclusion_rate,
            ROUND(SUM(m.t8_count)::numeric
                / NULLIF(SUM(m.deck_count), 0), 4)         AS card_t8_rate,
            ROUND(SUM(m.leader_total_t8s)::numeric
                / NULLIF(SUM(m.leader_total_decks), 0), 4) AS baseline_t8_rate,
            ROUND(
                (SUM(m.t8_count)::numeric / NULLIF(SUM(m.deck_count), 0))
                / NULLIF(SUM(m.leader_total_t8s)::numeric / NULLIF(SUM(m.leader_total_decks), 0), 0),
            4) AS conversion
        FROM mv_card_leader_stats m
        WHERE m.card_name = %s {set_filter}
          AND m.leader IS NOT NULL
          AND m.leader != ''
          AND EXISTS (
              SELECT 1 FROM cards c
              WHERE c.is_leader = true
                AND m.leader ILIKE c.name || '%%'
          )
        GROUP BY m.leader
        HAVING SUM(m.deck_count) >= 3
        ORDER BY SUM(m.deck_count) DESC
    """, params)

    return rows


@app.get("/api/card/{card_name}/copy-matrix")
def card_copy_matrix(
    card_name: str,
    set_code:  Optional[str] = Query(None),
):
    """
    For a single card: the MD-copies × SB-copies conversion matrix,
    broken down per leader.
    """
    params: list = [card_name]
    set_filter = ""
    if set_code:
        set_filter = "AND set_code = %s"
        params.append(set_code)

    rows = db.fetchall(f"""
        SELECT
            m.leader,
            m.md_copies,
            m.sb_copies,
            m.deck_count,
            m.t8_count,
            m.t8_rate,
            m.conversion
        FROM mv_card_copy_matrix m
        WHERE m.card_name = %s {set_filter}
          AND m.leader IS NOT NULL
          AND m.leader != ''
          AND EXISTS (
              SELECT 1 FROM cards c
              WHERE c.is_leader = true
                AND m.leader ILIKE c.name || '%%'
          )
        ORDER BY m.leader, m.md_copies, m.sb_copies
    """, params)

    # Pivot into { leader: { "md_X_sb_Y": { deck_count, conversion, ... } } }
    by_leader: dict = {}
    best_config: dict = {}

    for r in rows:
        ldr = r["leader"]
        if ldr not in by_leader:
            by_leader[ldr] = {}
        key = f"{r['md_copies']}m_{r['sb_copies']}s"
        by_leader[ldr][key] = {
            "md_copies":  r["md_copies"],
            "sb_copies":  r["sb_copies"],
            "deck_count": r["deck_count"],
            "t8_count":   r["t8_count"],
            "conversion": r["conversion"],
        }

        # Track best config per leader (highest conversion, min 5 decks)
        if r["deck_count"] >= 5:
            cur_best = best_config.get(ldr)
            if cur_best is None or (r["conversion"] or 0) > (cur_best["conversion"] or 0):
                best_config[ldr] = {
                    "md_copies":  r["md_copies"],
                    "sb_copies":  r["sb_copies"],
                    "conversion": r["conversion"],
                    "deck_count": r["deck_count"],
                }

    return {
        "card_name":   card_name,
        "by_leader":   by_leader,
        "best_config": best_config,
    }


# =============================================================================
#  EVENTS
# =============================================================================


@app.get("/api/events")
def events(
    set_code: Optional[str] = Query(None),
    limit:    int            = Query(100),
    offset:   int            = Query(0),
    format:   str            = Query("standard"),
):
    t = _tnames(format)
    params: list = []
    where = ""
    if set_code:
        where = "WHERE set_code = %s"
        params.append(set_code)

    rows = db.fetchall(f"""
        SELECT id, melee_id, name, date, venue, city, country,
               player_count, set_code, melee_url, scraped_at
        FROM {t['events']}
        {where}
        ORDER BY date DESC NULLS LAST
        LIMIT %s OFFSET %s
    """, params + [limit, offset])

    total = db.fetchone(f"SELECT COUNT(*) AS n FROM {t['events']} {where}", params or None)
    return {"events": rows, "total": total["n"] if total else 0}


# =============================================================================
#  CARDS  (served from local DB, seeded by scraper/swuapi.py)
# =============================================================================

@app.get("/api/cards")
def cards_list(
    name:     Optional[str] = Query(None),
    set_code: Optional[str] = Query(None),
    type:     Optional[str] = Query(None),
    is_leader: Optional[bool] = Query(None),
    is_base:   Optional[bool] = Query(None),
    limit:    int            = Query(200),
    offset:   int            = Query(0),
):
    filters = ["variant_type = 'Standard'"]
    params: list = []

    if name:
        filters.append("(name ILIKE %s OR subtitle ILIKE %s)")
        params += [f"%{name}%", f"%{name}%"]
    if set_code:
        filters.append("set_code = %s")
        params.append(set_code)
    if type:
        filters.append("type = %s")
        params.append(type)
    if is_leader is not None:
        filters.append("is_leader = %s")
        params.append(is_leader)
    if is_base is not None:
        filters.append("is_base = %s")
        params.append(is_base)

    where = "WHERE " + " AND ".join(filters)
    rows  = db.fetchall(
        f"SELECT * FROM cards {where} ORDER BY set_code, collector_number LIMIT %s OFFSET %s",
        params + [limit, offset]
    )
    return rows


@app.get("/api/cards/by-name/{name:path}")
def card_by_name(name: str, subtitle: Optional[str] = Query(None), response: Response = None):
    """
    Fuzzy lookup by card name — used to resolve decklist card names
    to their swuapi card record (for image URLs, aspects, etc.).
    Cached for 24h — card data is stable within a meta.
    """
    if response:
        response.headers["Cache-Control"] = "public, max-age=86400"
    # If subtitle provided, try exact name + subtitle first (pinpoints the right printing)
    if subtitle:
        row = db.fetchone(
            """SELECT * FROM cards
               WHERE name ILIKE %s AND subtitle ILIKE %s
                 AND variant_type = 'Standard' LIMIT 1""",
            (name, subtitle)
        )
        if row:
            return row

    # Exact name match — prefer newest set if multiple printings
    row = db.fetchone(
        """SELECT * FROM cards WHERE name ILIKE %s AND variant_type = 'Standard'
           ORDER BY COALESCE(
               (SELECT released_at FROM sets WHERE code = cards.set_code),
               '2000-01-01'::date
           ) DESC LIMIT 1""",
        (name,)
    )
    if row:
        return row

    # Partial match fallback
    row = db.fetchone(
        """SELECT * FROM cards WHERE name ILIKE %s AND variant_type = 'Standard'
           ORDER BY COALESCE(
               (SELECT released_at FROM sets WHERE code = cards.set_code),
               '2000-01-01'::date
           ) DESC LIMIT 1""",
        (f"%%{name}%%",)
    )
    if response:
        response.headers["Cache-Control"] = "public, max-age=3600"
    return row or {}


@app.get("/api/cards/leader-image/{leader_name:path}")
def leader_image(leader_name: str, response: Response):
    """
    Quick endpoint the frontend uses to get a leader's image URL.
    Cache is pre-warmed at startup so this should always be a memory hit.
    """
    response.headers["Cache-Control"] = "public, max-age=86400"
    if leader_name in _leader_image_cache:
        return _leader_image_cache[leader_name]

    parts    = leader_name.split(",", 1)
    name     = parts[0].strip()
    subtitle = parts[1].strip() if len(parts) > 1 else None

    row = None
    if subtitle:
        row = db.fetchone(
            """SELECT uuid, name, subtitle, front_image_url, back_image_url,
                      aspects, collector_number, set_code
               FROM cards
               WHERE is_leader = true AND variant_type = 'Standard'
                 AND name ILIKE %s AND subtitle ILIKE %s
               LIMIT 1""",
            (name, subtitle)
        )
    if not row:
        row = db.fetchone(
            """SELECT uuid, name, subtitle, front_image_url, back_image_url,
                      aspects, collector_number, set_code
               FROM cards
               WHERE is_leader = true AND variant_type = 'Standard'
                 AND name ILIKE %s
               ORDER BY COALESCE(
                   (SELECT released_at FROM sets WHERE code = cards.set_code),
                   '2000-01-01'::date
               ) DESC LIMIT 1""",
            (name,)
        )

    result = row or {}
    if result:
        _leader_image_cache[leader_name] = result
    return result



@app.on_event("startup")
def warm_leader_image_cache():
    """Pre-load all leader images at startup so requests never hit the DB cold."""
    try:
        rows = db.fetchall(
            """SELECT DISTINCT ON (name, subtitle)
                      name, subtitle, uuid, front_image_url, back_image_url,
                      aspects, collector_number, set_code
               FROM cards
               WHERE is_leader = true AND variant_type = 'Standard'
               ORDER BY name, subtitle, set_code DESC"""
        )
        for r in rows:
            key = r['name'] + (', ' + r['subtitle'] if r.get('subtitle') else '')
            _leader_image_cache[key] = r
        print(f"[startup] Leader image cache warmed: {len(_leader_image_cache)} entries")
    except Exception as e:
        print(f"[startup] Leader image cache warm failed: {e}")


# =============================================================================
#  METAS  (served from local DB, seeded by scraper/swuapi.py)
# =============================================================================

@app.get("/api/metas/dropdown")
def metas_dropdown(format: str = Query("premiere")):
    """Formatted for the frontend set/meta filter dropdowns."""
    rows = db.fetchall(
        "SELECT id, name, is_current FROM metas WHERE format = %s ORDER BY start_date DESC NULLS LAST",
        (format,)
    )
    return {"options": rows}




# =============================================================================
#  HEAD-TO-HEAD MATCHUPS
# =============================================================================

@app.get("/api/leader/{leader}/matchups")
def leader_matchups(
    leader:     str,
    meta_id:    Optional[str] = Query(None),
    base_group: Optional[str] = Query(None),
    min_games:  int            = Query(3),
    top8_only:  bool           = Query(False),
    format:     str            = Query("standard"),
):
    """
    Win/loss record for a leader against each opponent leader+base combo.
    base_group filters to only matches where THIS leader used those bases.
    top8_only restricts to matches where at least one player made top 8.
    """
    t = _tnames(format)
    date_sql, date_params = meta_date_filter(meta_id)
    own_bases = [b.strip() for b in base_group.split(',') if b.strip()] if base_group else []

    own_as_p1  = "AND m.p1_base = ANY(%s::text[])" if own_bases else ""
    own_as_p2  = "AND m.p2_base = ANY(%s::text[])" if own_bases else ""
    own_param  = [own_bases] if own_bases else []

    top8_join  = ""
    top8_where = ""
    if top8_only:
        top8_join  = f"""
            LEFT JOIN {t['standings']} s1t ON s1t.id = m.p1_standing_id
            LEFT JOIN {t['standings']} s2t ON s2t.id = m.p2_standing_id"""
        top8_where = """
            AND (
                s1t.placement <= GREATEST(CEIL(e.player_count::numeric * 0.08)::INT, 1)
                OR
                s2t.placement <= GREATEST(CEIL(e.player_count::numeric * 0.08)::INT, 1)
            )"""

    rows = db.fetchall(f"""
        WITH as_p1 AS (
            SELECT m.p2_leader AS opponent,
                   m.p2_base   AS opponent_base,
                   COUNT(*)::INT AS matches,
                   COUNT(*) FILTER (WHERE m.winner = 'p1')::INT AS wins,
                   COUNT(*) FILTER (WHERE m.winner = 'p2')::INT AS losses
            FROM {t['matches']} m
            JOIN {t['events']} e ON e.id = m.event_id
            {top8_join}
            WHERE m.p1_leader = %s AND m.p2_leader IS NOT NULL AND m.p2_base IS NOT NULL
              AND m.p1_leader != m.p2_leader AND m.winner IS NOT NULL
              {date_sql} {own_as_p1} {top8_where}
            GROUP BY m.p2_leader, m.p2_base
        ),
        as_p2 AS (
            SELECT m.p1_leader AS opponent,
                   m.p1_base   AS opponent_base,
                   COUNT(*)::INT AS matches,
                   COUNT(*) FILTER (WHERE m.winner = 'p2')::INT AS wins,
                   COUNT(*) FILTER (WHERE m.winner = 'p1')::INT AS losses
            FROM {t['matches']} m
            JOIN {t['events']} e ON e.id = m.event_id
            {top8_join}
            WHERE m.p2_leader = %s AND m.p1_leader IS NOT NULL AND m.p1_base IS NOT NULL
              AND m.p1_leader != m.p2_leader AND m.winner IS NOT NULL
              {date_sql} {own_as_p2} {top8_where}
            GROUP BY m.p1_leader, m.p1_base
        )
        SELECT opponent, opponent_base,
               SUM(matches)::INT AS matches,
               SUM(wins)::INT    AS wins,
               SUM(losses)::INT  AS losses
        FROM (SELECT * FROM as_p1 UNION ALL SELECT * FROM as_p2) t
        GROUP BY opponent, opponent_base
        ORDER BY SUM(matches) DESC
    """, [leader] + date_params + own_param + [leader] + date_params + own_param)

    if not rows:
        return []

    # Classify each opponent_base into a base group (same logic as matchup_matrix_by_base)
    all_base_names = list({r['opponent_base'] for r in rows if r['opponent_base']})
    if all_base_names:
        ph = ','.join(['%s'] * len(all_base_names))
        base_cards = db.fetchall(f"""
            SELECT DISTINCT ON (name) name,
                   COALESCE(aspects[1], 'none') AS aspect,
                   rarity, card_text, deploy_box, epic_action
            FROM cards
            WHERE is_base = true AND variant_type = 'Standard'
              AND name IN ({ph})
            ORDER BY name, set_code DESC
        """, all_base_names)
        base_meta = {r['name']: r for r in base_cards}
    else:
        base_meta = {}

    groups: dict = {}
    for r in rows:
        meta    = base_meta.get(r['opponent_base'], {})
        aspect  = meta.get('aspect', 'none') or 'none'
        rarity  = meta.get('rarity', 'Common') or 'Common'
        ability = _base_ability_type(
            meta.get('card_text') or '',
            meta.get('deploy_box') or '',
            meta.get('epic_action') or '',
            r['opponent_base'] or '',
        )
        grp_key = _base_group_key(aspect, ability, rarity, r['opponent_base'] or '')
        grp_lbl = _base_group_label(aspect, ability, rarity, r['opponent_base'] or '')
        combo   = f"{r['opponent']}|||{grp_key}"
        if combo not in groups:
            groups[combo] = {
                'opponent':            r['opponent'],
                'opponent_base_group': grp_lbl,
                'opponent_base_key':   grp_key,
                'bases':               [],
                'matches':             0,
                'wins':                0,
                'losses':              0,
            }
        if r['opponent_base'] not in groups[combo]['bases']:
            groups[combo]['bases'].append(r['opponent_base'])
        groups[combo]['matches'] += r['matches']
        groups[combo]['wins']    += r['wins']
        groups[combo]['losses']  += r['losses']

    result = []
    for g in groups.values():
        total = g['wins'] + g['losses']
        if total >= min_games:
            result.append({
                **g,
                'win_rate': round(g['wins'] / total, 4) if total > 0 else None,
            })

    result.sort(key=lambda x: (-x['matches'], -(x['win_rate'] or 0)))
    return result


@app.get("/api/leader/{leader}/matchup-cards")
def leader_matchup_cards(
    leader:         str,
    opponent:       str,
    opponent_bases: str            = Query("", description="Comma-separated opponent base names"),
    base_group:     Optional[str]  = Query(None, description="Comma-separated base names for this leader"),
    meta_id:        Optional[str]  = Query(None),
    min_games:      int             = Query(3),
    top8_only:      bool            = Query(False),
    format:         str             = Query("standard"),
):
    """
    Cards that over/underperform for {leader} vs {opponent}+bases.
    win_rate per card vs baseline_win_rate; sorted by delta DESC.
    """
    t = _tnames(format)
    date_sql, date_params = meta_date_filter(meta_id)

    own_bases = [b.strip() for b in base_group.split(',') if b.strip()] if base_group else []
    opp_bases = [b.strip() for b in opponent_bases.split(',') if b.strip()] if opponent_bases else []

    own_as_p1  = "AND m.p1_base = ANY(%s::text[])" if own_bases else ""
    own_as_p2  = "AND m.p2_base = ANY(%s::text[])" if own_bases else ""
    opp_as_p1  = "AND m.p2_base = ANY(%s::text[])" if opp_bases else ""
    opp_as_p2  = "AND m.p1_base = ANY(%s::text[])" if opp_bases else ""
    own_param  = [own_bases] if own_bases else []
    opp_param  = [opp_bases] if opp_bases else []

    top8_join  = ""
    top8_where = ""
    if top8_only:
        top8_join  = f"""
            LEFT JOIN {t['standings']} s1t ON s1t.id = m.p1_standing_id
            LEFT JOIN {t['standings']} s2t ON s2t.id = m.p2_standing_id"""
        top8_where = """
            AND (
                s1t.placement <= GREATEST(CEIL(e.player_count::numeric * 0.08)::INT, 1)
                OR
                s2t.placement <= GREATEST(CEIL(e.player_count::numeric * 0.08)::INT, 1)
            )"""

    branch_params = [leader, opponent] + date_params + own_param + opp_param

    rows = db.fetchall(f"""
        WITH matchup_games AS (
            SELECT m.p1_standing_id AS standing_id,
                   (m.winner = 'p1') AS won
            FROM {t['matches']} m
            JOIN {t['events']} e ON e.id = m.event_id
            {top8_join}
            WHERE m.p1_leader = %s AND m.p2_leader = %s
              AND m.winner IS NOT NULL
              {date_sql} {own_as_p1} {opp_as_p1} {top8_where}
            UNION ALL
            SELECT m.p2_standing_id AS standing_id,
                   (m.winner = 'p2') AS won
            FROM {t['matches']} m
            JOIN {t['events']} e ON e.id = m.event_id
            {top8_join}
            WHERE m.p2_leader = %s AND m.p1_leader = %s
              AND m.winner IS NOT NULL
              {date_sql} {own_as_p2} {opp_as_p2} {top8_where}
        ),
        totals AS (
            SELECT COUNT(*)::INT AS total_games,
                   SUM(CASE WHEN won THEN 1 ELSE 0 END)::INT AS total_wins
            FROM matchup_games
        ),
        card_stats AS (
            SELECT dc.card_name,
                   COUNT(*)::INT AS game_count,
                   SUM(CASE WHEN mg.won THEN 1 ELSE 0 END)::INT AS card_wins
            FROM matchup_games mg
            JOIN {t['decklist_cards']} dc ON dc.standing_id = mg.standing_id
            WHERE dc.is_sideboard = false
            GROUP BY dc.card_name
            HAVING COUNT(*) >= %s
        )
        SELECT cs.card_name,
               cs.game_count,
               cs.card_wins AS wins,
               ROUND(cs.card_wins::numeric / NULLIF(cs.game_count, 0), 4) AS win_rate,
               t.total_games,
               t.total_wins,
               ROUND(t.total_wins::numeric / NULLIF(t.total_games, 0), 4) AS baseline_win_rate,
               ROUND(
                   cs.card_wins::numeric / NULLIF(cs.game_count, 0)
                   - t.total_wins::numeric / NULLIF(t.total_games, 0),
               4) AS delta
        FROM card_stats cs, totals t
        ORDER BY delta DESC
    """, branch_params + branch_params + [min_games])

    return rows


@app.get("/api/matchups")
def matchup_matrix(
    meta_id:   Optional[str] = Query(None),
    min_games: int            = Query(5),
):
    """
    Full symmetric matchup matrix: every leader vs every leader.
    Returns flattened list of {leader, opponent, matches, wins, losses, win_rate}.
    """
    date_sql, date_params = meta_date_filter(meta_id)

    if date_sql:
        rows = db.fetchall(f"""
            WITH raw AS (
                SELECT m.p1_leader AS leader, m.p2_leader AS opponent,
                       COUNT(*)::INT AS matches,
                       COUNT(*) FILTER (WHERE m.winner = 'p1')::INT AS wins,
                       COUNT(*) FILTER (WHERE m.winner = 'p2')::INT AS losses
                FROM matches m
                JOIN events e ON e.id = m.event_id
                WHERE m.p1_leader IS NOT NULL AND m.p2_leader IS NOT NULL
                  AND m.p1_leader != m.p2_leader AND m.winner IS NOT NULL
                  {date_sql}
                GROUP BY m.p1_leader, m.p2_leader
            ),
            sym AS (
                SELECT leader, opponent, matches, wins, losses FROM raw
                UNION ALL
                SELECT opponent, leader, matches, losses, wins FROM raw
            )
            SELECT leader, opponent,
                   SUM(matches)::INT AS matches,
                   SUM(wins)::INT    AS wins,
                   SUM(losses)::INT  AS losses,
                   ROUND(SUM(wins)::numeric / NULLIF(SUM(wins)+SUM(losses),0), 4) AS win_rate
            FROM sym
            GROUP BY leader, opponent
            HAVING SUM(matches) >= %s
            ORDER BY leader, SUM(matches) DESC
        """, date_params + [min_games])
    else:
        rows = db.fetchall("""
            WITH sym AS (
                SELECT leader, opponent, matches, wins, losses FROM mv_leader_matchups
                UNION ALL
                SELECT opponent, leader, matches, losses, wins FROM mv_leader_matchups
            )
            SELECT leader, opponent,
                   SUM(matches)::INT AS matches,
                   SUM(wins)::INT    AS wins,
                   SUM(losses)::INT  AS losses,
                   ROUND(SUM(wins)::numeric / NULLIF(SUM(wins)+SUM(losses),0), 4) AS win_rate
            FROM sym
            GROUP BY leader, opponent
            HAVING SUM(matches) >= %s
            ORDER BY leader, SUM(matches) DESC
        """, [min_games])

    return rows


# =============================================================================
#  CARD SYNERGY PAIRS
# =============================================================================

@app.get("/api/leader/{leader}/synergy")
def leader_synergy(
    leader:     str,
    min_co:     int   = Query(10, description="Minimum co-occurrence count"),
    min_lift:   float = Query(1.1, description="Minimum lift (1.0 = independent)"),
    limit:      int   = Query(30),
    base_group: Optional[str] = Query(None),
):
    """
    Card synergy pairs for a leader.
    When base_group is provided, runs a live query filtered to those bases.
    Otherwise serves from the materialized view.
    lift > 1 = positively correlated (played together more than chance).
    """
    base_names = [b.strip() for b in base_group.split(',') if b.strip()] if base_group else []

    if base_names:
        # Live query scoped to specific bases
        ph = ','.join(['%s'] * len(base_names))
        rows = db.fetchall(f"""
            WITH leader_decks AS (
                SELECT COUNT(DISTINCT s.id)::INT AS total_decks
                FROM standings s
                JOIN events e ON e.id = s.event_id
                INNER JOIN (SELECT DISTINCT standing_id FROM decklist_cards) has_dl
                    ON has_dl.standing_id = s.id
                WHERE s.leader = %s AND s.base IN ({ph})
            ),
            card_incl AS (
                SELECT dc.card_name, COUNT(DISTINCT s.id)::INT AS deck_count
                FROM decklist_cards dc
                JOIN standings s ON s.id = dc.standing_id
                WHERE dc.is_sideboard = false
                  AND s.leader = %s AND s.base IN ({ph})
                GROUP BY dc.card_name
            ),
            pairs AS (
                SELECT dc1.card_name AS card_a, dc2.card_name AS card_b,
                       COUNT(DISTINCT s.id)::INT AS co_occur
                FROM decklist_cards dc1
                JOIN decklist_cards dc2
                  ON  dc2.standing_id = dc1.standing_id
                  AND dc2.is_sideboard = false
                  AND dc2.card_name > dc1.card_name
                JOIN standings s ON s.id = dc1.standing_id
                WHERE dc1.is_sideboard = false
                  AND s.leader = %s AND s.base IN ({ph})
                GROUP BY dc1.card_name, dc2.card_name
                HAVING COUNT(DISTINCT s.id) >= %s
            )
            SELECT p.card_a, p.card_b, p.co_occur,
                   a.deck_count AS incl_a, b.deck_count AS incl_b,
                   ld.total_decks,
                   ROUND(p.co_occur::numeric
                     / NULLIF((a.deck_count::numeric * b.deck_count::numeric)
                              / NULLIF(ld.total_decks, 0), 0), 3) AS lift
            FROM pairs p
            JOIN card_incl a ON a.card_name = p.card_a
            JOIN card_incl b ON b.card_name = p.card_b
            CROSS JOIN leader_decks ld
            WHERE ROUND(p.co_occur::numeric
                     / NULLIF((a.deck_count::numeric * b.deck_count::numeric)
                              / NULLIF(ld.total_decks, 0), 0), 3) >= %s
            ORDER BY lift DESC, co_occur DESC
            LIMIT %s
        """, [leader] + base_names + [leader] + base_names + [leader] + base_names + [min_co, min_lift, limit])
    else:
        rows = db.fetchall("""
            SELECT card_a, card_b, co_occur, incl_a, incl_b, total_decks, lift
            FROM mv_card_synergy
            WHERE leader = %s
              AND co_occur >= %s
              AND lift >= %s
              AND lift IS NOT NULL
            ORDER BY lift DESC, co_occur DESC
            LIMIT %s
        """, [leader, min_co, min_lift, limit])
    return rows


# =============================================================================
#  LEADER WEAKNESSES — cards with high win rates against a leader+base
# =============================================================================

@app.get("/api/leader/{leader}/weaknesses")
def leader_weaknesses(
    leader:     str,
    base_group: Optional[str] = Query(None),
    format:     str           = Query("standard"),
    meta_id:    Optional[str] = Query(None),
    min_games:  int           = Query(20),
    limit:      int           = Query(60),
    sort:       str           = Query("count"),  # "count" | "delta"
):
    """
    Cards with the highest win rates when played against this leader+base combo,
    regardless of the opponent deck/leader they're played in.
    """
    t = _tnames(format)
    date_sql, date_params = meta_date_filter(meta_id)

    base_names = [b.strip() for b in base_group.split(',') if b.strip()] if base_group else []
    base_as_p1 = "AND m.p1_base = ANY(%s::text[])" if base_names else ""
    base_as_p2 = "AND m.p2_base = ANY(%s::text[])" if base_names else ""
    base_param = [base_names] if base_names else []

    rows = db.fetchall(f"""
        WITH target_matches AS (
            SELECT m.p2_standing_id AS standing_id,
                   (m.winner = 'p2') AS won
            FROM {t['matches']} m
            JOIN {t['events']} e ON e.id = m.event_id
            WHERE m.p1_leader = %s AND m.winner IS NOT NULL
              {date_sql} {base_as_p1}
            UNION ALL
            SELECT m.p1_standing_id AS standing_id,
                   (m.winner = 'p1') AS won
            FROM {t['matches']} m
            JOIN {t['events']} e ON e.id = m.event_id
            WHERE m.p2_leader = %s AND m.winner IS NOT NULL
              {date_sql} {base_as_p2}
        ),
        totals AS (
            SELECT COUNT(*)::INT AS total_games,
                   SUM(CASE WHEN won THEN 1 ELSE 0 END)::INT AS total_wins
            FROM target_matches
        ),
        card_stats AS (
            SELECT dc.card_name,
                   COUNT(*)::INT AS game_count,
                   SUM(CASE WHEN tm.won THEN 1 ELSE 0 END)::INT AS wins
            FROM target_matches tm
            JOIN {t['decklist_cards']} dc ON dc.standing_id = tm.standing_id
            WHERE dc.is_sideboard = false
              AND NOT EXISTS (
                  SELECT 1 FROM cards c
                  WHERE c.name = SPLIT_PART(dc.card_name, ' | ', 1)
                    AND (c.is_leader = true OR c.is_base = true)
              )
            GROUP BY dc.card_name
            HAVING COUNT(*) >= %s
        )
        SELECT cs.card_name,
               cs.game_count,
               cs.wins,
               ROUND(cs.wins::numeric / NULLIF(cs.game_count, 0), 4) AS win_rate,
               t.total_games,
               t.total_wins,
               ROUND(t.total_wins::numeric / NULLIF(t.total_games, 0), 4) AS baseline_win_rate,
               ROUND(
                   cs.wins::numeric / NULLIF(cs.game_count, 0)
                   - t.total_wins::numeric / NULLIF(t.total_games, 0),
               4) AS delta
        FROM card_stats cs, totals t
        ORDER BY {'game_count DESC' if sort == 'count' else 'win_rate ASC'}, game_count DESC
        LIMIT %s
    """, [leader] + date_params + base_param +
         [leader] + date_params + base_param +
         [min_games, limit])

    return rows


# =============================================================================
#  LEADER+BASE MATCHUP MATRIX
# =============================================================================

@app.get("/api/matrix")
def matchup_matrix_by_base(
    meta_id:   Optional[str] = Query(None),
    min_decks: int            = Query(1,   description="Min decks for a combo to appear"),
    min_games: int            = Query(1,   description="Min H2H games for a cell to show"),
    top_n:     int            = Query(40,  description="Max combos to include"),
    top8_only: bool           = Query(False, description="Only count matches where at least one player made top 8"),
    format:    str            = Query("standard"),
):
    """
    Win-rate matrix of top leader+base combos vs each other.
    Rows = player's deck, columns = opponent's deck.
    win_rate > 0.5 means the row deck wins more than 50%% vs that column.
    """
    t = _tnames(format)
    date_sql, date_params = meta_date_filter(meta_id)

    # Step 1: get the top combos by deck count
    combos_raw = db.fetchall(f"""
        SELECT s.leader, s.base,
               COUNT(DISTINCT s.id)::INT AS decks
        FROM {t['standings']} s
        JOIN {t['events']} e ON e.id = s.event_id
        WHERE s.leader IS NOT NULL AND s.leader != ''
          AND s.base   IS NOT NULL AND s.base   != ''
          {date_sql}
        GROUP BY s.leader, s.base
        HAVING COUNT(DISTINCT s.id) >= %s
        ORDER BY decks DESC
        LIMIT %s
    """, date_params + [min_decks, top_n * 3])  # fetch extra to group by base_group

    if not combos_raw:
        return {"combos": [], "matrix": []}

    # Classify into base groups
    all_base_names = list({r["base"] for r in combos_raw})
    ph = ','.join(['%s'] * len(all_base_names))
    card_info = db.fetchall(f"""
        SELECT DISTINCT ON (name) name,
               COALESCE(aspects[1], 'none') AS aspect,
               rarity, card_text, deploy_box, epic_action
        FROM cards
        WHERE is_base = true AND variant_type = 'Standard'
          AND name IN ({ph})
        ORDER BY name, set_code DESC
    """, all_base_names)
    base_meta = {r["name"]: r for r in card_info}

    # Aggregate into leader+base_group combos
    groups: dict = {}
    for r in combos_raw:
        meta   = base_meta.get(r["base"], {})
        aspect = meta.get("aspect", "none") or "none"
        rarity = meta.get("rarity", "Common") or "Common"
        ability = _base_ability_type(
            meta.get("card_text") or "",
            meta.get("deploy_box") or "",
            meta.get("epic_action") or "",
            r["base"]
        )
        grp_key = _base_group_key(aspect, ability, rarity, r["base"])
        grp_lbl = _base_group_label(aspect, ability, rarity, r["base"])
        combo   = f"{r['leader']}|||{grp_key}"
        if combo not in groups:
            groups[combo] = {
                "leader":     r["leader"],
                "base_group": grp_lbl,
                "base_key":   grp_key,
                "bases":      [],
                "decks":      0,
            }
        groups[combo]["decks"]  += r["decks"]
        if r["base"] not in groups[combo]["bases"]:
            groups[combo]["bases"].append(r["base"])

    # Sort by decks, take top_n
    combos = sorted(groups.values(), key=lambda x: -x["decks"])[:top_n]
    if not combos:
        return {"combos": [], "matrix": []}

    # Step 2: H2H win rates — use denorm leader/base columns directly (no standings join)
    all_leaders = list({c["leader"] for c in combos})
    ldr_ph = ','.join(['%s'] * len(all_leaders))

    # top8_join and top8_where are paired: the join brings in placement data,
    # the where clause filters to matches involving at least one top-8 finisher.
    # Both must be interpolated into the same query or neither should be.
    top8_join  = ""
    top8_where = ""
    if top8_only:
        # Only matches where at least one player made top 8 in their event
        top8_join  = f"""
            LEFT JOIN {t['standings']} s1t ON s1t.id = m.p1_standing_id
            LEFT JOIN {t['standings']} s2t ON s2t.id = m.p2_standing_id
            LEFT JOIN {t['events']}    et  ON et.id  = m.event_id"""
        top8_where = """
            AND (
                s1t.placement <= GREATEST(CEIL(et.player_count::numeric * 0.08)::INT, 1)
                OR
                s2t.placement <= GREATEST(CEIL(et.player_count::numeric * 0.08)::INT, 1)
            )"""

    matches = db.fetchall(f"""
        SELECT m.p1_leader, m.p1_base, m.p2_leader, m.p2_base, m.winner
        FROM {t['matches']} m
        JOIN {t['events']} e ON e.id = m.event_id
        {top8_join}
        WHERE m.winner IS NOT NULL
          AND m.p1_leader IS NOT NULL AND m.p2_leader IS NOT NULL
          AND m.p1_leader IN ({ldr_ph}) AND m.p2_leader IN ({ldr_ph})
          AND m.p1_leader != m.p2_leader
          {date_sql}
          {top8_where}
    """, all_leaders + all_leaders + date_params)

    # Map base -> combo_key for fast lookup
    base_to_combo: dict = {}
    for c in combos:
        for b in c["bases"]:
            base_to_combo[f"{c['leader']}||{b}"] = f"{c['leader']}|||{c['base_key']}"

    # Accumulate W/L per ordered combo pair.
    # Games are stored symmetrically: both (A,B) and (B,A) get the game count,
    # but wins are directional — only the winner's key gets the win increment.
    # This lets matrix[row][col] = pair_stats[(row_key, col_key)] directly.
    from collections import defaultdict
    pair_stats: dict = defaultdict(lambda: {"wins": 0, "games": 0})

    for m in matches:
        p1_key = base_to_combo.get(f"{m['p1_leader']}||{m['p1_base']}")
        p2_key = base_to_combo.get(f"{m['p2_leader']}||{m['p2_base']}")
        if not p1_key or not p2_key or p1_key == p2_key:
            continue
        pair_stats[(p1_key, p2_key)]["games"] += 1
        pair_stats[(p2_key, p1_key)]["games"] += 1
        if m["winner"] == "p1":
            pair_stats[(p1_key, p2_key)]["wins"] += 1
        else:
            pair_stats[(p2_key, p1_key)]["wins"] += 1

    combo_keys = [f"{c['leader']}|||{c['base_key']}" for c in combos]

    # Build matrix rows
    matrix = []
    for row_c in combos:
        row_key  = f"{row_c['leader']}|||{row_c['base_key']}"
        row_cells = []
        for col_c in combos:
            col_key = f"{col_c['leader']}|||{col_c['base_key']}"
            if row_key == col_key:
                row_cells.append(None)  # diagonal
                continue
            stat = pair_stats.get((row_key, col_key), {"wins": 0, "games": 0})
            if stat["games"] < min_games:
                row_cells.append(None)
            else:
                row_cells.append({
                    "wins":  stat["wins"],
                    "games": stat["games"],
                    "wr":    round(stat["wins"] / stat["games"], 4),
                })
        matrix.append(row_cells)

    # Compute overall win rate for each combo by summing across all opponents.
    # Diagonal entries (mirror matchups) are skipped since a combo can't play itself.
    overall = []
    for ri, row_c in enumerate(combos):
        row_key = f"{row_c['leader']}|||{row_c['base_key']}"
        total_wins = total_games = 0
        for col_c in combos:
            col_key = f"{col_c['leader']}|||{col_c['base_key']}"
            if row_key == col_key:
                continue
            stat = pair_stats.get((row_key, col_key), {"wins": 0, "games": 0})
            total_wins  += stat["wins"]
            total_games += stat["games"]
        overall.append({
            "wins":  total_wins,
            "games": total_games,
            "wr":    round(total_wins / total_games, 4) if total_games >= min_games else None,
        })

    return {
        "combos": [{"leader": c["leader"], "base_group": c["base_group"],
                    "base_key": c["base_key"], "bases": c["bases"],
                    "decks": c["decks"]} for c in combos],
        "matrix":  matrix,
        "overall": overall,
    }


@app.get("/api/events/recent-top8")
def recent_top8(
    limit:   int = Query(20, ge=1, le=100),
    meta_id: Optional[str] = Query(None),
):
    """Recent events with their top 8 standings, for the decklists page."""
    date_sql, date_params = meta_date_filter(meta_id)

    events = db.fetchall(f"""
        SELECT e.id, e.name, e.date, e.player_count, e.melee_url,
               e.venue, e.country, e.set_code
        FROM events e
        WHERE e.player_count IS NOT NULL
          {date_sql}
        ORDER BY e.date DESC, e.id DESC
        LIMIT %s
    """, date_params + [limit])

    if not events:
        return []

    event_ids = [e['id'] for e in events]
    placeholders = ','.join(['%s'] * len(event_ids))
    standings = db.fetchall(f"""
        SELECT
            s.event_id,
            s.placement,
            s.player_name,
            s.leader,
            s.base,
            s.decklist_url,
            s.has_decklist,
            s.match_wins,
            s.match_losses
        FROM standings s
        WHERE s.event_id IN ({placeholders})
          AND s.placement <= 8
          AND s.placement IS NOT NULL
        ORDER BY s.event_id, s.placement
    """, event_ids)

    by_event = {}
    for row in standings:
        eid = row['event_id']
        if eid not in by_event:
            by_event[eid] = []
        by_event[eid].append(row)

    return [
        {**ev, 'top8': by_event.get(ev['id'], [])}
        for ev in events
        if by_event.get(ev['id'])
    ]


@app.get("/api/events/{event_id}/top8")
def event_top8(event_id: int):
    """Top 8 standings for a single event with player name, leader, base, decklist URL."""
    rows = db.fetchall("""
        SELECT
            s.id,
            s.placement,
            s.player_name,
            s.leader,
            s.base,
            s.decklist_url,
            s.has_decklist,
            s.match_wins,
            s.match_losses
        FROM standings s
        WHERE s.event_id = %s
          AND s.placement <= GREATEST(CEIL(
              (SELECT player_count FROM events WHERE id = %s)::numeric * 0.08
          )::INT, 1)
          AND s.placement IS NOT NULL
        ORDER BY s.placement
    """, [event_id, event_id])
    return rows


@app.get("/api/players")
def players(
    limit:   int = Query(100, ge=1, le=500),
    offset:  int = Query(0,   ge=0),
    search:  Optional[str] = Query(None),
    meta_id: Optional[str] = Query(None),
):
    """Player leaderboard — grouped by stable identity UUID."""
    date_sql, date_params = meta_date_filter(meta_id)
    search_sql   = "AND (m.display_name ILIKE %s OR s.player_name ILIKE %s)" if search else ""
    search_param = ['%' + search + '%', '%' + search + '%'] if search else []

    rows = db.fetchall(f"""
        WITH mapped AS (
            SELECT
                -- If identity map has been bootstrapped, use identity UUID.
                -- Otherwise fall back to normalized player name so multiple
                -- per-tournament IDs for the same person aggregate correctly.
                COALESCE(
                    m.identity_id::TEXT,
                    LOWER(TRIM(s.player_name))
                ) AS identity_id,
                COALESCE(m.display_name, s.player_name) AS display_name,
                s.placement,
                s.leader,
                s.base,
                e.id   AS event_id,
                e.date AS event_date,
                e.player_count
            FROM standings s
            JOIN events e ON e.id = s.event_id
            LEFT JOIN player_id_map m ON m.melee_player_id = s.melee_player_id
                AND m.status != 'rejected'
            WHERE s.melee_player_id IS NOT NULL
              AND e.player_count IS NOT NULL
              {date_sql}
              {search_sql}
        )
        SELECT
            identity_id,
            (ARRAY_AGG(display_name ORDER BY event_date DESC))[1] AS player_name,
            COUNT(DISTINCT event_id)::INT                          AS events_played,
            COUNT(*) FILTER (
                WHERE placement <= GREATEST(CEIL(player_count::numeric * 0.08)::INT, 1)
            )::INT                                                 AS top8s,
            COUNT(*) FILTER (WHERE placement = 1)::INT            AS wins,
            (ARRAY_AGG(
                CASE WHEN leader IS NOT NULL
                     THEN leader || COALESCE(' / ' || base, '') END
                ORDER BY event_date DESC
            ) FILTER (WHERE leader IS NOT NULL))[1]               AS last_deck,
            MAX(event_date)                                        AS last_played
        FROM mapped
        GROUP BY identity_id
        ORDER BY wins DESC, top8s DESC, events_played DESC
        LIMIT %s OFFSET %s
    """, date_params + search_param + [limit, offset])

    total = db.fetchone(f"""
        SELECT COUNT(DISTINCT COALESCE(m.identity_id::TEXT, LOWER(TRIM(s.player_name)))) AS total
        FROM standings s
        JOIN events e ON e.id = s.event_id
        LEFT JOIN player_id_map m ON m.melee_player_id = s.melee_player_id
            AND m.status != 'rejected'
        WHERE s.melee_player_id IS NOT NULL
          AND e.player_count IS NOT NULL
          {date_sql}
          AND (%s IS NULL OR m.display_name ILIKE %s OR s.player_name ILIKE %s)
    """, date_params + [search, '%' + search + '%' if search else None, '%' + search + '%' if search else None])

    return {"players": rows, "total": total["total"] if total else 0}


@app.get("/api/players/{identity_id}")
def player_detail(
    identity_id: str,
    meta_id:     Optional[str] = Query(None),
):
    """Full event history for a player identity (may span multiple melee IDs)."""
    date_sql, date_params = meta_date_filter(meta_id)

    rows = db.fetchall(f"""
        SELECT
            e.id          AS event_id,
            e.name        AS event_name,
            e.date        AS event_date,
            e.player_count,
            e.melee_url,
            s.placement,
            s.player_name,
            s.melee_player_id,
            s.leader,
            s.base,
            s.decklist_url,
            s.match_wins,
            s.match_losses,
            GREATEST(CEIL(e.player_count::numeric * 0.08)::INT, 1) AS top8_threshold
        FROM standings s
        JOIN events e ON e.id = s.event_id
        LEFT JOIN player_id_map m ON m.melee_player_id = s.melee_player_id
        WHERE COALESCE(m.identity_id::TEXT, LOWER(TRIM(s.player_name))) = %s
          AND COALESCE(m.status, 'confirmed') != 'rejected'
          AND e.player_count IS NOT NULL
          {date_sql}
        ORDER BY e.date DESC, e.id DESC
    """, [identity_id] + date_params)

    if not rows:
        raise HTTPException(status_code=404, detail="Player not found")

    return rows


# ── Player identity admin endpoints ──────────────────────────────────────────

@app.get("/api/admin/player-merges")
def player_merge_suggestions(
    status: Optional[str] = Query('review'),
):
    """Get pending merge suggestions for admin review."""
    rows = db.fetchall("""
        SELECT
            pi.id            AS identity_id,
            pi.display_name,
            ARRAY_AGG(m.melee_player_id ORDER BY m.melee_player_id) AS melee_ids,
            ARRAY_AGG(m.confidence ORDER BY m.melee_player_id)       AS confidences,
            ARRAY_AGG(m.display_name ORDER BY m.melee_player_id)     AS names,
            -- Events per melee ID for context
            ARRAY_AGG(
                (SELECT COUNT(*) FROM standings s WHERE s.melee_player_id = m.melee_player_id)::INT
                ORDER BY m.melee_player_id
            ) AS event_counts
        FROM player_identities pi
        JOIN player_id_map m ON m.identity_id = pi.id
        WHERE (%s IS NULL OR m.status = %s)
        GROUP BY pi.id, pi.display_name
        HAVING COUNT(*) > 1
        ORDER BY pi.display_name
    """, [status, status])
    return rows


@app.post("/api/admin/player-merges/confirm")
def confirm_merge(body: dict):
    """Confirm a merge: set all melee_ids to point to identity_id with status=confirmed."""
    identity_id = body.get('identity_id')
    melee_ids   = body.get('melee_ids', [])
    if not identity_id or not melee_ids:
        raise HTTPException(status_code=400, detail="identity_id and melee_ids required")
    for mid in melee_ids:
        db.execute("""
            UPDATE player_id_map SET status='confirmed', confidence='manual'
            WHERE melee_player_id = %s AND identity_id = %s
        """, (mid, identity_id))
    return {"ok": True}


@app.post("/api/admin/player-merges/reject")
def reject_merge(body: dict):
    """Reject a merge: split the melee_id off into its own new identity."""
    melee_id = body.get('melee_id')
    name     = body.get('display_name', 'Unknown')
    if not melee_id:
        raise HTTPException(status_code=400, detail="melee_id required")
    # Create new identity for this ID
    new_id = db.fetchone(
        "INSERT INTO player_identities (display_name) VALUES (%s) RETURNING id",
        (name,)
    )['id']
    db.execute("""
        UPDATE player_id_map SET identity_id=%s, status='rejected', confidence='manual'
        WHERE melee_player_id = %s
    """, (new_id, melee_id))
    return {"ok": True, "new_identity_id": str(new_id)}


@app.post("/api/admin/player-merges/manual-merge")
def manual_merge(body: dict):
    """Manually merge two identity IDs into one (keeping the first as canonical)."""
    keep_id    = body.get('keep_identity_id')
    discard_id = body.get('discard_identity_id')
    if not keep_id or not discard_id:
        raise HTTPException(status_code=400, detail="keep_identity_id and discard_identity_id required")
    db.execute(
        "UPDATE player_id_map SET identity_id=%s, confidence='manual', status='confirmed' WHERE identity_id=%s",
        (keep_id, discard_id)
    )
    db.execute("DELETE FROM player_identities WHERE id=%s", (discard_id,))
    return {"ok": True}


@app.get("/api/deck/analyze")
def analyze_deck(url: str = Query(...)):
    """
    Fetch a deck from a swudb.com link and compare it to meta stats.
    e.g. url=https://swudb.com/deck/xcMDHCSSPxG
    """
    import requests as req
    import re as _re

    # Extract deck ID from URL
    m = _re.search(r'swudb\.com/deck/([A-Za-z0-9]+)', url)
    if not m:
        raise HTTPException(status_code=400, detail="Invalid swudb.com deck URL. Expected format: https://swudb.com/deck/DECKID")
    deck_id = m.group(1)

    # Fetch deck JSON from swudb
    try:
        resp = req.get(f"https://swudb.com/api/getDeckJson/{deck_id}", timeout=10,
                       headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
        deck_json = resp.json()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Could not fetch deck from swudb: {e}")

    leader_entry  = deck_json.get("leader") or {}
    base_entry    = deck_json.get("base")   or {}
    deck_entries  = deck_json.get("deck",      [])
    sb_entries    = deck_json.get("sideboard", [])
    leader_id     = leader_entry.get("id", "")
    base_id       = base_entry.get("id", "")

    if not leader_id:
        raise HTTPException(status_code=400, detail="Deck has no leader")

    # Resolve card IDs (format: SET_NUMBER e.g. LAW_006) to card names
    def parse_card_id(card_id):
        parts = card_id.split("_", 1)
        if len(parts) != 2: return None, None
        return parts[0], parts[1].lstrip("0") or "0"

    all_entries = deck_entries + sb_entries + [leader_entry, base_entry]
    all_ids = list({e["id"] for e in all_entries if e.get("id")})

    # Build query: match by set_code + number extracted from image URL
    # URL pattern: card_{set_prefix}{number_6digit}_EN_... 
    # e.g. LAW_006 -> set_code=LAW, number=6 -> image contains '010006'
    id_clauses, id_params = [], []
    for cid in all_ids:
        set_code, num = parse_card_id(cid)
        if set_code and num:
            padded = num.zfill(3)
            id_clauses.append("(set_code = %s AND front_image_url LIKE %s)")
            id_params.extend([set_code.upper(), f"%{padded}_%"])

    if not id_clauses:
        raise HTTPException(status_code=400, detail="Could not parse card IDs")

    rows = db.fetchall(f"""
        SELECT DISTINCT ON (set_code, name, subtitle)
            set_code,
            front_image_url,
            name,
            COALESCE(subtitle, '') AS subtitle,
            type   AS card_type,
            cost,
            arena,
            is_leader,
            is_base
        FROM cards
        WHERE variant_type = 'Standard'
          AND ({" OR ".join(id_clauses)})
        ORDER BY set_code, name, subtitle, set_code DESC
    """, id_params)

    # Build SET_NNN -> card lookup
    id_map = {}
    for r in rows:
        url_val = r.get("front_image_url") or ""
        m2 = _re.search(r'card_\d{2}(\d{3})(\d{3})_', url_val)
        if m2:
            num = str(int(m2.group(2)))
            key = f"{r['set_code']}_{num.zfill(3)}"
            subtitle = r['subtitle']
            full_name = r['name'] + (' | ' + subtitle if subtitle else '')
            id_map[key] = {**r, "full_name": full_name}

    # Resolve leader and base
    leader_card = id_map.get(leader_id)
    base_card   = id_map.get(base_id)

    if not leader_card:
        raise HTTPException(status_code=400, detail=f"Could not find leader: {leader_id}")

    leader_lookup = leader_card["name"] + (", " + leader_card["subtitle"] if leader_card["subtitle"] else "")
    base_name     = base_card["name"] if base_card else None

    # Get base label
    base_label = None
    if base_name:
        br = db.fetchone("SELECT label FROM base_reference WHERE name = %s", (base_name,))
        if br:
            base_label = br["label"]

    # Fetch archetype stats
    stats_rows = db.fetchall("""
        WITH cs AS (
            SELECT dc.card_name, dc.is_sideboard,
                   COUNT(DISTINCT s.id)::INT   AS deck_count,
                   ROUND(AVG(dc.quantity), 2)  AS avg_copies,
                   COUNT(DISTINCT s.id) FILTER (
                       WHERE s.placement <= GREATEST(CEIL(e.player_count::numeric * 0.08)::INT, 1)
                   )::INT AS t8_count
            FROM decklist_cards dc
            JOIN standings s ON s.id = dc.standing_id
            JOIN events e ON e.id = s.event_id
            WHERE s.leader = %s AND s.base = %s AND e.player_count IS NOT NULL
            GROUP BY dc.card_name, dc.is_sideboard
            HAVING COUNT(DISTINCT s.id) >= 3
        ),
        lt AS (
            SELECT
                COUNT(DISTINCT s.id)::INT AS total,
                COUNT(DISTINCT s.id) FILTER (
                    WHERE s.placement <= GREATEST(CEIL(e.player_count::numeric * 0.08)::INT, 1)
                )::INT AS total_t8s
            FROM standings s JOIN events e ON e.id = s.event_id
            WHERE s.leader = %s AND s.base = %s AND e.player_count IS NOT NULL
        )
        SELECT cs.*, lt.total AS leader_total, lt.total_t8s AS leader_total_t8s,
               ROUND(cs.deck_count::numeric / NULLIF(lt.total, 0), 4) AS inclusion_rate,
               ROUND(
                   (cs.t8_count::numeric / NULLIF(cs.deck_count, 0))
                   / NULLIF(lt.total_t8s::numeric / NULLIF(lt.total, 0), 0)
               , 4) AS conversion
        FROM cs, lt
        ORDER BY inclusion_rate DESC
    """, [leader_lookup, base_name, leader_lookup, base_name])

    stats_by_name = {(r['card_name'], r['is_sideboard']): r for r in stats_rows}
    leader_total  = stats_rows[0]["leader_total"] if stats_rows else 0

    def enrich(entries, is_sb):
        out = []
        for e in entries:
            card = id_map.get(e["id"])
            if not card:
                out.append({"card_id": e["id"], "count": e["count"], "name": e["id"], "found": False})
                continue
            name  = card["full_name"]
            stats = stats_by_name.get((name, is_sb)) or stats_by_name.get((name, not is_sb))
            out.append({
                "card_id":        e["id"],
                "count":          e["count"],
                "name":           name,
                "card_type":      card["card_type"],
                "cost":           card["cost"],
                "arena":          card["arena"],
                "found":          True,
                "inclusion_rate": float(stats["inclusion_rate"]) if stats else None,
                "conversion":     float(stats["conversion"])     if stats and stats["conversion"] else None,
                "avg_copies":     float(stats["avg_copies"])     if stats else None,
                "t8_count":       stats["t8_count"]              if stats else None,
                "deck_count":     stats["deck_count"]            if stats else None,
                "leader_total":   leader_total,
            })
        return out

    deck_cards = enrich(deck_entries, False)
    sb_cards   = enrich(sb_entries,   True)

    all_names = {c["name"] for c in deck_cards + sb_cards if c["found"]}
    missing = [
        {
            "name":           r["card_name"],
            "inclusion_rate": float(r["inclusion_rate"]),
            "conversion":     float(r["conversion"]) if r["conversion"] else None,
            "avg_copies":     float(r["avg_copies"]),
            "deck_count":     r["deck_count"],
            "leader_total":   leader_total,
            "t8_count":       r["t8_count"],
        }
        for r in stats_rows
        if r["card_name"] not in all_names and not r["is_sideboard"]
    ]

    return {
        "leader":       leader_lookup,
        "base":         base_name,
        "base_label":   base_label,
        "leader_total": leader_total,
        "deck_name":    deck_json.get("metadata", {}).get("name", ""),
        "deck":         deck_cards,
        "sideboard":    sb_cards,
        "missing":      missing[:20],
    }


@app.get("/api/debug/images")
def debug_images():
    """Quick check: how many cards have image URLs populated."""
    r = db.fetchone("""
        SELECT
            COUNT(*) FILTER (WHERE front_image_url IS NOT NULL) AS with_images,
            COUNT(*) FILTER (WHERE front_image_url IS NULL)     AS without_images,
            COUNT(*)                                             AS total,
            MIN(synced_at)                                       AS oldest_sync,
            MAX(synced_at)                                       AS newest_sync
        FROM cards
        WHERE variant_type = 'Standard'
    """)
    sample = db.fetchall("""
        SELECT name, front_image_url
        FROM cards WHERE variant_type = 'Standard' AND is_leader = true
        ORDER BY name LIMIT 5
    """)
    return {"stats": r, "leader_sample": sample}


# =============================================================================
#  SYNC STATUS
# =============================================================================

@app.get("/api/sync/status")
def sync_status():
    """Reports when each swuapi resource was last synced."""
    return db.fetchall("SELECT resource, synced_at, record_count FROM sync_state ORDER BY resource")


# =============================================================================
#  META OVERVIEW — Weekly wins + meta share with trend
# =============================================================================

@app.get("/api/meta-counter")
def meta_counter(
    weeks:  int = Query(2, description="Number of recent weeks to show"),
    format: str = Query("standard"),
):
    """
    Weekly meta overview: wins and deck share per leader per week,
    with week-on-week trend.
    Returns weeks most-recent-first, each with per-leader stats.
    """
    import traceback
    try:
        return _meta_counter_inner(weeks, format=format)
    except Exception as e:
        return {"error": str(e), "trace": traceback.format_exc()}


def _meta_counter_inner(weeks: int = 2, format: str = "standard"):
    t = _tnames(format)
    # Get distinct tournament weekends (Saturday-anchored), most recent first.
    # DOW: 0=Sunday,1=Monday,...,6=Saturday in PostgreSQL EXTRACT.
    # To find the Saturday of each event's weekend:
    #   - if the event is Saturday (DOW=6), use that date
    #   - if Sunday (DOW=0), go back 1 day to Saturday
    #   - otherwise snap forward to next Saturday
    # Simplest: Saturday = date - ((DOW + 1) % 7) gives the preceding/same Saturday.
    week_rows = db.fetchall(f"""
        SELECT DISTINCT
            (date::date - (((EXTRACT(DOW FROM date::timestamp)::int + 1) %% 7)) * INTERVAL '1 day')::date
                AS week_start,
            COUNT(DISTINCT id)::INT AS events
        FROM {t['events']}
        WHERE date IS NOT NULL
        GROUP BY week_start
        ORDER BY week_start DESC
        LIMIT %s
    """, [weeks + 1])  # fetch one extra to compute trend vs prior week

    if not week_rows:
        return {"weeks": []}

    week_starts = [r["week_start"] for r in week_rows]

    from datetime import timedelta as _td
    # For each weekend window (Sat–Sun) get per-leader stats
    result_weeks = []
    for i, ws in enumerate(week_starts[:weeks]):
        week_end = ws + _td(days=1)  # Saturday + 1 = Sunday

        rows = db.fetchall(f"""
            SELECT
                s.leader,
                s.base,
                COALESCE(c.aspects[1], 'none') AS base_aspect,
                COUNT(*)::INT                                          AS total_decks,
                COUNT(*) FILTER (WHERE s.placement = 1)::INT           AS wins,
                COUNT(*) FILTER (WHERE s.placement <=
                    GREATEST(CEIL(e.player_count::numeric * 0.08)::INT, 1))::INT AS top8s,
                ROUND(COUNT(*)::numeric /
                    NULLIF(SUM(COUNT(*)) OVER (), 0), 4)               AS meta_share
            FROM {t['standings']} s
            JOIN {t['events']} e ON e.id = s.event_id
            LEFT JOIN LATERAL (
                SELECT aspects FROM cards
                WHERE name = s.base AND is_base = true AND variant_type = 'Standard'
                ORDER BY set_code DESC LIMIT 1
            ) c ON true
            WHERE s.leader IS NOT NULL AND s.leader != ''
              AND s.base IS NOT NULL AND s.base != ''
              AND e.date BETWEEN %s AND %s
            GROUP BY s.leader, s.base, c.aspects
            HAVING COUNT(*) >= 2
            ORDER BY wins DESC, top8s DESC, total_decks DESC
        """, [ws, week_end])

        result_weeks.append({
            "week_start":  str(ws),
            "week_end":    str(week_end),
            "event_count": next((r["events"] for r in week_rows if r["week_start"] == ws), 0),
            "leaders":     [{"leader": r["leader"], "base": r["base"],
                             "base_aspect": r["base_aspect"] or "none",
                             "wins": int(r["wins"]),
                             "top8s": int(r["top8s"]), "total_decks": int(r["total_decks"]),
                             "meta_share": float(r["meta_share"] or 0)}
                            for r in rows],
        })

    # Compute week-on-week delta if we have 2+ weeks
    if len(result_weeks) >= 2:
        def _combo_key(r): return r["leader"] + "|||" + (r.get("base") or "")
        last_week = {_combo_key(r): r for r in result_weeks[1]["leaders"]}
        for row in result_weeks[0]["leaders"]:
            prev = last_week.get(_combo_key(row))
            row["wins_delta"]       = row["wins"]       - (prev["wins"]       if prev else 0)
            row["top8s_delta"]      = row["top8s"]      - (prev["top8s"]      if prev else 0)
            row["meta_share_delta"] = round(row["meta_share"] - (prev["meta_share"] if prev else 0), 4)

    return {"weeks": result_weeks}
