"""
scraper/sync_swuguru_metas.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Syncs the metas and meta_bans tables in `swuguru` from swuapi.com/metas.
Safe to run at any time — idempotent upsert.

Usage:
    python -m scraper.sync_swuguru_metas
"""

import logging
import os

import httpx
import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

API_URL = "https://api.swuapi.com/metas"

DDL = """
CREATE TABLE IF NOT EXISTS metas (
    id           TEXT        PRIMARY KEY,
    name         TEXT        NOT NULL,
    set_code     TEXT        REFERENCES sets(code),
    format       TEXT        NOT NULL,
    start_date   DATE,
    end_date     DATE,
    is_current   BOOLEAN     NOT NULL DEFAULT FALSE,
    card_pool    TEXT,
    synced_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS meta_bans (
    id             SERIAL      PRIMARY KEY,
    meta_id        TEXT        NOT NULL REFERENCES metas(id) ON DELETE CASCADE,
    card_name      TEXT        NOT NULL,
    card_id        TEXT        NOT NULL,
    action         TEXT        NOT NULL,
    effective_date DATE,
    format         TEXT,
    UNIQUE (meta_id, card_id)
);

CREATE INDEX IF NOT EXISTS meta_bans_meta_id ON meta_bans(meta_id);
CREATE INDEX IF NOT EXISTS meta_bans_card_id ON meta_bans(card_id);
"""


def get_conn():
    return psycopg2.connect(
        host=os.getenv("DB_HOST", "192.168.1.200"),
        port=int(os.getenv("DB_PORT", 5432)),
        dbname="swuguru",
        user=os.getenv("DB_USER", "swu_user"),
        password=os.getenv("DB_PASS", "981273465aA!"),
    )


def fetch_metas() -> list[dict]:
    log.info(f"Fetching {API_URL} …")
    r = httpx.get(API_URL, timeout=15)
    r.raise_for_status()
    metas = r.json()["metas"]
    log.info(f"  {len(metas)} metas received")
    return metas


def sync(metas: list[dict], cur):
    cur.execute(DDL)

    new_metas = 0
    updated_metas = 0
    new_bans = 0

    for m in metas:
        cur.execute("SELECT id, is_current FROM metas WHERE id = %s", (m["id"],))
        existing = cur.fetchone()

        cur.execute(
            """
            INSERT INTO metas (id, name, set_code, format, start_date, end_date, is_current, card_pool, synced_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, now())
            ON CONFLICT (id) DO UPDATE SET
                name       = EXCLUDED.name,
                set_code   = EXCLUDED.set_code,
                format     = EXCLUDED.format,
                start_date = EXCLUDED.start_date,
                end_date   = EXCLUDED.end_date,
                is_current = EXCLUDED.is_current,
                card_pool  = EXCLUDED.card_pool,
                synced_at  = now()
            """,
            (
                m["id"],
                m["name"],
                m.get("set"),
                m["format"],
                m.get("start"),
                m.get("end"),
                m.get("isCurrent", False),
                m.get("cardPool"),
            ),
        )

        if existing is None:
            new_metas += 1
            log.info(f"  + New meta: {m['id']} ({m['name']})")
        else:
            updated_metas += 1

        # Sync bans: delete stale, upsert current
        bans = m.get("bans") or []
        cur.execute("DELETE FROM meta_bans WHERE meta_id = %s", (m["id"],))
        for ban in bans:
            cur.execute(
                """
                INSERT INTO meta_bans (meta_id, card_name, card_id, action, effective_date, format)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (meta_id, card_id) DO UPDATE SET
                    card_name      = EXCLUDED.card_name,
                    action         = EXCLUDED.action,
                    effective_date = EXCLUDED.effective_date,
                    format         = EXCLUDED.format
                """,
                (
                    m["id"],
                    ban["card"],
                    ban["cardId"],
                    ban["action"],
                    ban.get("effectiveDate"),
                    ban.get("format"),
                ),
            )
            new_bans += 1

    log.info(
        f"Sync complete — {new_metas} new metas, {updated_metas} updated, "
        f"{new_bans} ban entries written"
    )

    # Log current metas
    cur.execute("SELECT id, name, is_current FROM metas WHERE is_current = TRUE ORDER BY id")
    current = cur.fetchall()
    if current:
        log.info(f"Current metas: {[r[0] for r in current]}")


def main():
    metas = fetch_metas()
    conn  = get_conn()
    try:
        with conn:
            cur = conn.cursor()
            sync(metas, cur)
            cur.close()
    finally:
        conn.close()


if __name__ == "__main__":
    main()
