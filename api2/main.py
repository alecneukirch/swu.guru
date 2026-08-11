"""
api2/main.py — swu.guru v2 FastAPI backend
Queries the `swuguru` database (clean schema seeded from swuapi + Melee).
"""

import os
import re
import time
from contextlib import asynccontextmanager
from functools import lru_cache
from pathlib import Path
from typing import Optional

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

load_dotenv()

# ── DB ────────────────────────────────────────────────────────────────────────

def get_conn():
    return psycopg2.connect(
        host=os.getenv("DB_HOST", "192.168.1.200"),
        port=int(os.getenv("DB_PORT", 5432)),
        dbname="swuguru",
        user=os.getenv("DB_USER", "swu_user"),
        password=os.getenv("DB_PASS", "981273465aA!"),
        cursor_factory=psycopg2.extras.RealDictCursor,
    )

def fetchall(sql: str, params=()) -> list[dict]:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return [dict(r) for r in cur.fetchall()]

def fetchone(sql: str, params=()) -> Optional[dict]:
    rows = fetchall(sql, params)
    return rows[0] if rows else None

# ── Filter helpers ────────────────────────────────────────────────────────────

def event_filter(meta_id: Optional[str] = None, event_level: Optional[str] = None) -> tuple[str, list]:
    """Returns (WHERE clause additions, params) for filtering events."""
    clauses, params = [], []
    if meta_id:
        clauses.append("""
            e.date BETWEEN (SELECT start_date FROM metas WHERE id = %s)
                       AND  COALESCE((SELECT end_date FROM metas WHERE id = %s), CURRENT_DATE)
        """)
        params += [meta_id, meta_id]
    if event_level:
        clauses.append("lower(e.event_level) LIKE %s")
        params.append(f"%{event_level.lower()}%")
    where = ("AND " + " AND ".join(clauses)) if clauses else ""
    return where, params

# ── App ───────────────────────────────────────────────────────────────────────

FRONTEND_DIR = Path(__file__).parent.parent / "frontend2" / "dist"

@asynccontextmanager
async def lifespan(app: FastAPI):
    yield

app = FastAPI(title="swu.guru v2", lifespan=lifespan)

# ── Static frontend ───────────────────────────────────────────────────────────

if FRONTEND_DIR.exists():
    app.mount("/assets", StaticFiles(directory=str(FRONTEND_DIR / "assets")), name="assets")

# ── /api/metas/dropdown ───────────────────────────────────────────────────────

@app.get("/api/metas/dropdown")
def metas_dropdown():
    rows = fetchall("""
        SELECT id,
               name || CASE WHEN is_current THEN ' (current)' ELSE '' END AS label,
               is_current,
               start_date,
               end_date
        FROM metas
        WHERE format = 'premiere'
        ORDER BY COALESCE(start_date, '2099-01-01'::date) DESC
    """)
    return rows

# ── /api/summary ─────────────────────────────────────────────────────────────

@app.get("/api/summary")
def summary(
    meta_id:     Optional[str] = None,
    event_level: Optional[str] = None,
):
    ef, ep = event_filter(meta_id, event_level)
    row = fetchone(f"""
        SELECT
            COUNT(DISTINCT e.id)    AS events,
            COUNT(DISTINCT s.id)    AS entries,
            COUNT(DISTINCT s.leader) AS leaders,
            MAX(e.date)             AS latest_event
        FROM standings s
        JOIN events e ON e.id = s.event_id
        WHERE s.leader IS NOT NULL {ef}
    """, ep)
    return row or {}

# ── /api/leaders ──────────────────────────────────────────────────────────────

@app.get("/api/leaders")
def leaders(
    meta_id:     Optional[str] = None,
    event_level: Optional[str] = None,
    min_entries: int = Query(10, ge=1),
):
    ef, ep = event_filter(meta_id, event_level)
    rows = fetchall(f"""
        WITH base_lookup AS (
            SELECT DISTINCT ON (c.name)
                c.name AS base_name,
                COALESCE(
                    (SELECT bg.canonical_name FROM base_groups bg WHERE bg.canonical_name = c.name LIMIT 1),
                    (SELECT bg.canonical_name FROM base_groups bg
                     WHERE bg.hp = c.hp
                       AND bg.color = CASE c.aspects[1]
                         WHEN 'Aggression' THEN 'Red'
                         WHEN 'Command'    THEN 'Green'
                         WHEN 'Cunning'    THEN 'Yellow'
                         WHEN 'Vigilance'  THEN 'Blue'
                         ELSE c.aspects[1] END
                       AND bg.rarity_class = 'common'
                     LIMIT 1)
                ) AS base_group
            FROM cards c WHERE c.is_base = TRUE
        ),
        match_wins AS (
            SELECT p1_leader AS leader, bl.base_group,
                   SUM(CASE WHEN m.winner='p1' THEN 1 ELSE 0 END) AS wins,
                   COUNT(*) AS games
            FROM matches m JOIN events e ON e.id = m.event_id
            LEFT JOIN base_lookup bl ON bl.base_name = m.p1_base
            WHERE m.winner IN ('p1','p2') {ef}
            GROUP BY p1_leader, bl.base_group
            UNION ALL
            SELECT p2_leader AS leader, bl.base_group,
                   SUM(CASE WHEN m.winner='p2' THEN 1 ELSE 0 END) AS wins,
                   COUNT(*) AS games
            FROM matches m JOIN events e ON e.id = m.event_id
            LEFT JOIN base_lookup bl ON bl.base_name = m.p2_base
            WHERE m.winner IN ('p1','p2') {ef}
            GROUP BY p2_leader, bl.base_group
        ),
        match_stats AS (
            SELECT leader, base_group,
                   SUM(wins)::float / NULLIF(SUM(games), 0) AS win_rate,
                   SUM(games) AS total_matches
            FROM match_wins GROUP BY leader, base_group
        ),
        base AS (
            SELECT
                s.leader,
                bl.base_group,
                COUNT(*)                                                    AS entries,
                AVG(s.game_win_rate)                                        AS game_win_rate,
                COUNT(*) FILTER (WHERE s.placement <= 8)                   AS top8s,
                COUNT(*) FILTER (WHERE s.placement = 1)                    AS event_wins
            FROM standings s
            JOIN events e ON e.id = s.event_id
            LEFT JOIN base_lookup bl ON bl.base_name = s.base
            WHERE s.leader IS NOT NULL {ef}
            GROUP BY s.leader, bl.base_group
            HAVING COUNT(*) >= %s
        ),
        totals AS (SELECT SUM(entries) AS total FROM base),
        ranked AS (
            SELECT b.*, ROW_NUMBER() OVER (ORDER BY b.entries DESC) AS rank
            FROM base b
        ),
        aspect_lookup AS (
            SELECT
                name || ', ' || subtitle AS leader_key,
                CASE
                    WHEN 'Villainy' = ANY(aspects) THEN 'Villainy'
                    WHEN 'Heroism'  = ANY(aspects) THEN 'Heroism'
                    ELSE aspects[1]
                END AS primary_aspect
            FROM cards
            WHERE is_leader = TRUE AND variant_type = 'Standard'
        ),
        baseline AS (
            SELECT SUM(top8s)::float / NULLIF(SUM(entries), 0) AS meta_t8_rate FROM base
        )
        SELECT
            r.*,
            ms.win_rate,
            ms.total_matches,
            r.top8s::float / NULLIF(r.entries, 0)   AS top8_rate,
            r.entries::float / NULLIF(t.total, 0)   AS meta_share,
            ROUND(
                ((r.top8s::float / NULLIF(r.entries, 0))
                / NULLIF(bsl.meta_t8_rate, 0))::numeric, 3
            )                                        AS conversion,
            al.primary_aspect
        FROM ranked r
        LEFT JOIN match_stats ms ON ms.leader = r.leader
                                AND ms.base_group IS NOT DISTINCT FROM r.base_group
        LEFT JOIN aspect_lookup al ON al.leader_key = r.leader
        CROSS JOIN totals t
        CROSS JOIN baseline bsl
        ORDER BY r.entries DESC
    """, ep + ep + [min_entries])

    total = sum(r["entries"] for r in rows)
    return {"leaders": rows, "total_entries": total}

# ── /api/leader/{leader}/stats ────────────────────────────────────────────────

@app.get("/api/leader/{leader}/stats")
def leader_stats(
    leader:      str,
    meta_id:     Optional[str] = None,
    event_level: Optional[str] = None,
):
    ef, ep = event_filter(meta_id, event_level)
    row = fetchone(f"""
        WITH match_stats AS (
            SELECT
                SUM(CASE WHEN (m.p1_leader=%s AND m.winner='p1') OR (m.p2_leader=%s AND m.winner='p2') THEN 1 ELSE 0 END)::float
                    / NULLIF(COUNT(*), 0) AS win_rate,
                COUNT(*) AS total_matches
            FROM matches m JOIN events e ON e.id = m.event_id
            WHERE (m.p1_leader=%s OR m.p2_leader=%s) AND m.winner IN ('p1','p2') {ef}
        ),
        base AS (
            SELECT
                COUNT(*)                                     AS entries,
                AVG(s.game_win_rate)                         AS game_win_rate,
                COUNT(*) FILTER (WHERE s.placement <= 8)    AS top8s,
                COUNT(*) FILTER (WHERE s.placement = 1)     AS event_wins
            FROM standings s
            JOIN events e ON e.id = s.event_id
            WHERE s.leader = %s {ef}
        ),
        total AS (
            SELECT COUNT(*) AS t
            FROM standings s
            JOIN events e ON e.id = s.event_id
            WHERE s.leader IS NOT NULL {ef}
        ),
        field_t8 AS (
            SELECT SUM(CASE WHEN s.placement <= 8 THEN 1 ELSE 0 END)::float
                   / NULLIF(COUNT(*), 0) AS rate
            FROM standings s JOIN events e ON e.id = s.event_id
            WHERE s.leader IS NOT NULL {ef}
        ),
        aspect_lookup AS (
            SELECT
                CASE WHEN 'Villainy' = ANY(aspects) THEN 'Villainy'
                     WHEN 'Heroism'  = ANY(aspects) THEN 'Heroism'
                     ELSE aspects[1] END AS primary_aspect
            FROM cards
            WHERE is_leader = TRUE AND variant_type = 'Standard'
              AND (name || ', ' || subtitle = %s OR name = %s)
            LIMIT 1
        ),
        funnel AS (
            SELECT
                ROUND(COUNT(*) FILTER (WHERE s.placement <= GREATEST(CEIL(e.player_count * 0.50)::int, 1))::numeric
                      / NULLIF(COUNT(*), 0) / 0.50, 3) AS t50_conv,
                ROUND(COUNT(*) FILTER (WHERE s.placement <= GREATEST(CEIL(e.player_count * 0.25)::int, 1))::numeric
                      / NULLIF(COUNT(*), 0) / 0.25, 3) AS t25_conv,
                ROUND(COUNT(*) FILTER (WHERE s.placement <= GREATEST(CEIL(e.player_count * 0.10)::int, 1))::numeric
                      / NULLIF(COUNT(*), 0) / 0.10, 3) AS t10_conv,
                ROUND(COUNT(*) FILTER (WHERE s.placement <= GREATEST(CEIL(e.player_count * 0.01)::int, 1))::numeric
                      / NULLIF(COUNT(*), 0) / 0.01, 3) AS t1_conv
            FROM standings s JOIN events e ON e.id = s.event_id
            WHERE s.leader = %s AND e.player_count > 0 {ef}
        )
        SELECT b.*, ms.win_rate, ms.total_matches,
               b.entries::float / NULLIF(t.t, 0)   AS meta_share,
               b.top8s::float  / NULLIF(b.entries, 0) AS top8_rate,
               ROUND(((b.top8s::float / NULLIF(b.entries, 0))
                      / NULLIF(ft.rate, 0))::numeric, 3) AS conversion,
               al.primary_aspect,
               f.t50_conv, f.t25_conv, f.t10_conv, f.t1_conv
        FROM base b, total t, match_stats ms, field_t8 ft, aspect_lookup al, funnel f
    """, [leader, leader, leader, leader] + ep + [leader] + ep + ep + ep + [leader, leader] + [leader] + ep)
    if not row or not row.get("entries"):
        raise HTTPException(404, f"Leader not found: {leader}")
    return row

# ── /api/leader/{leader}/cards ────────────────────────────────────────────────

@app.get("/api/leader/{leader}/cards")
def leader_cards(
    leader:      str,
    meta_id:     Optional[str] = None,
    event_level: Optional[str] = None,
    min_decks:   int = Query(3, ge=1),
):
    ef, ep = event_filter(meta_id, event_level)
    rows = fetchall(f"""
        WITH decks AS (
            SELECT s.id AS standing_id, s.match_wins, s.match_losses, s.match_draws
            FROM standings s
            JOIN events e ON e.id = s.event_id
            WHERE s.leader = %s
              AND s.has_decklist = TRUE {ef}
        ),
        totals AS (SELECT COUNT(*) AS n FROM decks)
        SELECT
            dc.card_name,
            dc.is_sideboard,
            COUNT(DISTINCT dc.standing_id)                                               AS deck_count,
            COUNT(DISTINCT dc.standing_id)::float / NULLIF((SELECT n FROM totals), 0)   AS copy_rate,
            AVG(dc.quantity)                                                             AS avg_copies,
            SUM(d.match_wins)::float
                / NULLIF(SUM(d.match_wins + d.match_losses + COALESCE(d.match_draws,0)), 0) AS card_mwr,
            c.type,
            c.arena,
            c.cost
        FROM decklist_cards dc
        JOIN decks d ON d.standing_id = dc.standing_id
        LEFT JOIN LATERAL (
            SELECT type, arena, cost FROM cards
            WHERE (name || ' | ' || COALESCE(subtitle,'') = dc.card_name
                   OR name = dc.card_name)
              AND variant_type = 'Standard'
            LIMIT 1
        ) c ON TRUE
        GROUP BY dc.card_name, dc.is_sideboard, c.type, c.arena, c.cost
        HAVING COUNT(DISTINCT dc.standing_id) >= %s
        ORDER BY copy_rate DESC, dc.card_name
    """, [leader] + ep + [min_decks])
    return rows

# ── /api/leader/{leader}/matchups ─────────────────────────────────────────────

@app.get("/api/leader/{leader}/matchups")
def leader_matchups(
    leader:      str,
    meta_id:     Optional[str] = None,
    event_level: Optional[str] = None,
    min_games:   int = Query(5, ge=1),
):
    ef, ep = event_filter(meta_id, event_level)
    rows = fetchall(f"""
        WITH m AS (
            SELECT
                CASE WHEN m.p1_leader = %s THEN m.p2_leader ELSE m.p1_leader END AS opponent,
                CASE WHEN m.p1_leader = %s THEN m.winner = 'p1'
                                           ELSE m.winner = 'p2' END AS won
            FROM matches m
            JOIN events e ON e.id = m.event_id
            WHERE (m.p1_leader = %s OR m.p2_leader = %s)
              AND m.p1_leader IS NOT NULL AND m.p2_leader IS NOT NULL
              AND m.p1_leader != m.p2_leader
              AND m.winner IN ('p1','p2') {ef}
        )
        SELECT
            opponent,
            COUNT(*) FILTER (WHERE won)     AS wins,
            COUNT(*) FILTER (WHERE NOT won)  AS losses,
            COUNT(*)                         AS games,
            AVG(won::int)::numeric(5,4)      AS win_rate
        FROM m
        WHERE opponent IS NOT NULL
        GROUP BY opponent
        HAVING COUNT(*) >= %s
        ORDER BY win_rate DESC
    """, [leader, leader, leader, leader] + ep + [min_games])
    return rows

# ── /api/leader/{leader}/weaknesses ───────────────────────────────────────────

@app.get("/api/leader/{leader}/weaknesses")
def leader_weaknesses(
    leader:      str,
    meta_id:     Optional[str] = None,
    event_level: Optional[str] = None,
    min_games:   int = Query(5, ge=1),
):
    """Cards that opponents play that correlate with beating this leader."""
    ef, ep = event_filter(meta_id, event_level)

    # Baseline: overall win rate of opponents vs this leader
    baseline = fetchone(f"""
        SELECT AVG((m.winner != CASE WHEN m.p1_leader = %s THEN 'p1' ELSE 'p2' END)::int) AS baseline_wr
        FROM matches m
        JOIN events e ON e.id = m.event_id
        WHERE (m.p1_leader = %s OR m.p2_leader = %s)
          AND m.winner IN ('p1','p2') {ef}
    """, [leader, leader, leader] + ep)
    baseline_wr = (baseline or {}).get("baseline_wr") or 0.5

    rows = fetchall(f"""
        WITH matchups AS (
            SELECT
                m.id AS match_id,
                CASE WHEN m.p1_leader = %s THEN m.p2_standing_id ELSE m.p1_standing_id END AS opp_standing_id,
                CASE WHEN m.p1_leader = %s THEN m.winner != 'p1'
                                           ELSE m.winner != 'p2' END AS opp_won
            FROM matches m
            JOIN events e ON e.id = m.event_id
            WHERE (m.p1_leader = %s OR m.p2_leader = %s)
              AND m.winner IN ('p1','p2')
              AND m.p1_leader != m.p2_leader {ef}
        )
        SELECT
            dc.card_name,
            COUNT(*)                     AS games,
            AVG(mu.opp_won::int)         AS opp_win_rate,
            AVG(mu.opp_won::int) - %s    AS delta
        FROM matchups mu
        JOIN decklist_cards dc ON dc.standing_id = mu.opp_standing_id
        WHERE dc.is_sideboard = FALSE
        GROUP BY dc.card_name
        HAVING COUNT(*) >= %s
        ORDER BY opp_win_rate DESC
        LIMIT 50
    """, [leader, leader, leader, leader] + ep + [baseline_wr, min_games])
    return rows

# ── /api/leader/{leader}/mirror ───────────────────────────────────────────────

@app.get("/api/leader/{leader}/mirror")
def leader_mirror(
    leader:    str,
    meta_id:   Optional[str] = None,
    event_level: Optional[str] = None,
    min_games: int = Query(3, ge=1),
):
    ef, ep = event_filter(meta_id, event_level)

    baseline = fetchone(f"""
        SELECT 0.5 AS baseline_wr
    """, [])
    baseline_wr = 0.5

    rows = fetchall(f"""
        WITH mirror AS (
            SELECT
                m.id,
                m.p1_standing_id,
                m.p2_standing_id,
                m.winner
            FROM matches m
            JOIN events e ON e.id = m.event_id
            WHERE m.p1_leader = %s AND m.p2_leader = %s
              AND m.winner IN ('p1','p2') {ef}
        ),
        winners AS (
            SELECT
                CASE WHEN winner = 'p1' THEN p1_standing_id ELSE p2_standing_id END AS win_sid,
                CASE WHEN winner = 'p2' THEN p1_standing_id ELSE p2_standing_id END AS lose_sid
            FROM mirror
        )
        SELECT
            dc.card_name,
            COUNT(*)                   AS games,
            AVG((dc.standing_id = w.win_sid)::int) AS win_rate,
            AVG((dc.standing_id = w.win_sid)::int) - 0.5 AS delta
        FROM winners w
        JOIN decklist_cards dc ON dc.standing_id IN (w.win_sid, w.lose_sid)
        WHERE dc.is_sideboard = FALSE
        GROUP BY dc.card_name
        HAVING COUNT(*) >= %s
        ORDER BY win_rate DESC
        LIMIT 60
    """, [leader, leader] + ep + [min_games])
    return rows

# ── /api/matchups (matrix) ────────────────────────────────────────────────────

@app.get("/api/matchups")
def matchups(
    meta_id:     Optional[str] = None,
    event_level: Optional[str] = None,
    min_entries: int = Query(10, ge=1),
    min_games:   int = Query(5, ge=1),
):
    ef, ep = event_filter(meta_id, event_level)

    # Leaders that meet min_entries threshold
    eligible = fetchall(f"""
        SELECT s.leader
        FROM standings s
        JOIN events e ON e.id = s.event_id
        WHERE s.leader IS NOT NULL {ef}
        GROUP BY s.leader
        HAVING COUNT(*) >= %s
        ORDER BY COUNT(*) DESC
    """, ep + [min_entries])
    leader_list = [r["leader"] for r in eligible]
    if not leader_list:
        return {"leaders": [], "matchups": []}

    rows = fetchall(f"""
        WITH raw AS (
            SELECT
                p1_leader AS leader, p2_leader AS opponent,
                SUM(CASE WHEN m.winner='p1' THEN 1 ELSE 0 END) AS wins,
                SUM(CASE WHEN m.winner='p2' THEN 1 ELSE 0 END) AS losses,
                COUNT(*) AS games
            FROM matches m
            JOIN events e ON e.id = m.event_id
            WHERE p1_leader = ANY(%s) AND p2_leader = ANY(%s)
              AND p1_leader != p2_leader
              AND m.winner IN ('p1','p2') {ef}
            GROUP BY p1_leader, p2_leader
            UNION ALL
            SELECT
                p2_leader, p1_leader,
                SUM(CASE WHEN m.winner='p2' THEN 1 ELSE 0 END),
                SUM(CASE WHEN m.winner='p1' THEN 1 ELSE 0 END),
                COUNT(*)
            FROM matches m
            JOIN events e ON e.id = m.event_id
            WHERE p1_leader = ANY(%s) AND p2_leader = ANY(%s)
              AND p1_leader != p2_leader
              AND m.winner IN ('p1','p2') {ef}
            GROUP BY p2_leader, p1_leader
        )
        SELECT
            leader, opponent,
            SUM(wins)   AS wins,
            SUM(losses) AS losses,
            SUM(games)  AS games,
            SUM(wins)::float / NULLIF(SUM(wins + losses), 0) AS win_rate
        FROM raw
        GROUP BY leader, opponent
        HAVING SUM(games) >= %s
    """, [leader_list, leader_list] + ep + [leader_list, leader_list] + ep + [min_games])

    return {"leaders": leader_list, "matchups": rows}

# ── /api/meta-call ────────────────────────────────────────────────────────────

@app.get("/api/meta-call")
def meta_call(
    meta_id:     Optional[str] = None,
    event_level: Optional[str] = None,
    min_entries: int = Query(10, ge=1),
):
    ef, ep = event_filter(meta_id, event_level)

    leaders_data = fetchall(f"""
        SELECT
            s.leader,
            COUNT(*)             AS entries,
            AVG(s.match_win_rate) AS win_rate
        FROM standings s
        JOIN events e ON e.id = s.event_id
        WHERE s.leader IS NOT NULL {ef}
        GROUP BY s.leader
        HAVING COUNT(*) >= %s
    """, ep + [min_entries])

    if not leaders_data:
        return []

    total = sum(r["entries"] for r in leaders_data)
    meta_shares = {r["leader"]: r["entries"] / total for r in leaders_data}
    win_rates   = {r["leader"]: float(r["win_rate"] or 0.5) for r in leaders_data}
    leader_list = list(meta_shares.keys())

    # Matchup matrix
    mu_rows = fetchall(f"""
        WITH raw AS (
            SELECT p1_leader AS l, p2_leader AS o,
                   SUM(CASE WHEN m.winner='p1' THEN 1 ELSE 0 END) AS w, COUNT(*) AS g
            FROM matches m JOIN events e ON e.id = m.event_id
            WHERE p1_leader = ANY(%s) AND p2_leader = ANY(%s)
              AND m.winner IN ('p1','p2') {ef}
            GROUP BY p1_leader, p2_leader
            UNION ALL
            SELECT p2_leader, p1_leader,
                   SUM(CASE WHEN m.winner='p2' THEN 1 ELSE 0 END), COUNT(*)
            FROM matches m JOIN events e ON e.id = m.event_id
            WHERE p1_leader = ANY(%s) AND p2_leader = ANY(%s)
              AND m.winner IN ('p1','p2') {ef}
            GROUP BY p2_leader, p1_leader
        )
        SELECT l, o, SUM(w)::float/NULLIF(SUM(g),0) AS wr
        FROM raw GROUP BY l, o HAVING SUM(g) >= 5
    """, [leader_list, leader_list] + ep + [leader_list, leader_list] + ep)

    mu = {(r["l"], r["o"]): r["wr"] for r in mu_rows}

    results = []
    for leader in leader_list:
        score = 0.0
        for opp, share in meta_shares.items():
            if opp == leader:
                continue
            wr = mu.get((leader, opp)) or win_rates.get(leader, 0.5)
            score += wr * share
        results.append({
            "leader":     leader,
            "score":      round(score, 4),
            "win_rate":   win_rates[leader],
            "entries":    next(r["entries"] for r in leaders_data if r["leader"] == leader),
            "meta_share": meta_shares[leader],
        })

    results.sort(key=lambda r: r["score"], reverse=True)
    return results

# ── /api/cards/card-image ────────────────────────────────────────────────────

@app.get("/api/cards/card-image/{card_name:path}")
def card_image(card_name: str):
    row = fetchone("""
        SELECT front_image_url FROM cards
        WHERE (name || ' | ' || COALESCE(subtitle,'') = %s
               OR name || ', ' || COALESCE(subtitle,'') = %s
               OR name = %s)
          AND is_leader = FALSE AND is_base = FALSE
          AND variant_type = 'Standard'
        LIMIT 1
    """, [card_name, card_name, card_name])
    if row and row.get("front_image_url"):
        return RedirectResponse(row["front_image_url"])
    raise HTTPException(404)

# ── /api/cards/leader-image (proxy from old API or swuapi) ───────────────────

@app.get("/api/cards/leader-image/{leader_name:path}")
def leader_image(leader_name: str):
    # Look up the leader card image from the cards table
    row = fetchone("""
        SELECT back_image_url, front_image_url FROM cards
        WHERE is_leader = TRUE
          AND (name = %s
               OR name || ' | ' || subtitle = %s
               OR name || ', ' || subtitle = %s)
          AND variant_type = 'Standard'
        LIMIT 1
    """, [leader_name, leader_name, leader_name])
    if row:
        url = row.get("back_image_url") or row.get("front_image_url")
        if url:
            return RedirectResponse(url)
    raise HTTPException(404)

# ── SPA fallback — must be last so API routes match first ────────────────────

@app.get("/favicon.svg", include_in_schema=False)
def favicon():
    f = FRONTEND_DIR / "favicon.svg"
    if f.exists():
        return FileResponse(str(f))
    raise HTTPException(404)

@app.get("/", include_in_schema=False)
@app.get("/{path:path}", include_in_schema=False)
def spa_fallback(path: str = ""):
    idx = FRONTEND_DIR / "index.html"
    if idx.exists():
        return FileResponse(str(idx))
    raise HTTPException(503, "Frontend not built")
