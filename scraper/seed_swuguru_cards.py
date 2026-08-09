"""
scraper/seed_swuguru_cards.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Creates and populates the `sets`, `cards`, and `base_groups` tables in
the `swuguru` database from swuapi.com.

Usage:
    python -m scraper.seed_swuguru_cards
    python -m scraper.seed_swuguru_cards --wipe   # drop and recreate tables first
"""

import argparse
import json
import logging
import time

import httpx
import psycopg2
import psycopg2.extras
from dotenv import load_dotenv
import os

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

API_URL            = "https://api.swuapi.com/export/all"
BASE_GROUPS_API    = "https://api.swuapi.com/base-groups"

DDL = """
CREATE TABLE IF NOT EXISTS sets (
    uuid         TEXT        PRIMARY KEY,
    code         TEXT        NOT NULL UNIQUE,
    name         TEXT        NOT NULL,
    release_date DATE,
    total_cards  INTEGER,
    created_at   TIMESTAMPTZ,
    updated_at   TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS cards (
    uuid              TEXT        PRIMARY KEY,
    external_id       INTEGER,
    card_id           TEXT        NOT NULL,   -- e.g. "ASH_001"
    external_uid      TEXT,
    name              TEXT        NOT NULL,
    subtitle          TEXT,
    set_code          TEXT        REFERENCES sets(code),
    card_number       TEXT,
    serial_code       TEXT,
    type              TEXT,
    type2             TEXT,
    rarity            TEXT,
    cost              INTEGER,
    power             INTEGER,
    hp                INTEGER,
    arena             TEXT,
    aspects           TEXT[]      NOT NULL DEFAULT '{}',
    traits            TEXT[]      NOT NULL DEFAULT '{}',
    keywords          TEXT[]      NOT NULL DEFAULT '{}',
    front_text        TEXT,
    back_text         TEXT,
    deploy_box        TEXT,
    rules             TEXT,
    double_sided      BOOLEAN     NOT NULL DEFAULT FALSE,
    epic_action       TEXT,
    variant_type      TEXT,
    is_unique         BOOLEAN     NOT NULL DEFAULT FALSE,
    is_leader         BOOLEAN     NOT NULL DEFAULT FALSE,
    is_base           BOOLEAN     NOT NULL DEFAULT FALSE,
    front_image_url   TEXT,
    back_image_url    TEXT,
    artist            TEXT,
    full_text         TEXT,
    additional_rulings TEXT[]     NOT NULL DEFAULT '{}',
    synced_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS cards_set_code    ON cards(set_code);
CREATE INDEX IF NOT EXISTS cards_type        ON cards(type);
CREATE INDEX IF NOT EXISTS cards_is_leader   ON cards(is_leader) WHERE is_leader = TRUE;
CREATE INDEX IF NOT EXISTS cards_is_base     ON cards(is_base)   WHERE is_base   = TRUE;
CREATE INDEX IF NOT EXISTS cards_variant     ON cards(variant_type);
CREATE INDEX IF NOT EXISTS cards_name        ON cards(name);

CREATE TABLE IF NOT EXISTS base_groups (
    uuid          TEXT    PRIMARY KEY,
    canonical_name TEXT   NOT NULL UNIQUE,
    color         TEXT,
    hp            INTEGER,
    rarity_class  TEXT,
    created_at    TIMESTAMPTZ,
    updated_at    TIMESTAMPTZ
);
"""

DROP_DDL = """
DROP TABLE IF EXISTS cards      CASCADE;
DROP TABLE IF EXISTS sets       CASCADE;
DROP TABLE IF EXISTS base_groups CASCADE;
"""


def get_conn():
    return psycopg2.connect(
        host=os.getenv("DB_HOST", "192.168.1.200"),
        port=int(os.getenv("DB_PORT", 5432)),
        dbname="swuguru",
        user=os.getenv("DB_USER", "swu_user"),
        password=os.getenv("DB_PASS", "981273465aA!"),
    )


def fetch_export() -> dict:
    log.info(f"Fetching {API_URL} …")
    r = httpx.get(API_URL, timeout=60)
    r.raise_for_status()
    data = r.json()
    log.info(
        f"  {data['meta']['totalCards']} cards, "
        f"{data['meta']['totalSets']} sets — "
        f"scraped {data['meta']['lastScrapedAt']}"
    )
    return data


def upsert_sets(cur, sets: list[dict]):
    rows = [
        (
            s["uuid"],
            s["code"],
            s["name"],
            s.get("release_date"),
            s.get("total_cards"),
            s.get("created_at"),
            s.get("updated_at"),
        )
        for s in sets
    ]
    psycopg2.extras.execute_values(
        cur,
        """
        INSERT INTO sets (uuid, code, name, release_date, total_cards, created_at, updated_at)
        VALUES %s
        ON CONFLICT (uuid) DO UPDATE SET
            code         = EXCLUDED.code,
            name         = EXCLUDED.name,
            release_date = EXCLUDED.release_date,
            total_cards  = EXCLUDED.total_cards,
            updated_at   = EXCLUDED.updated_at
        """,
        rows,
    )
    log.info(f"  Upserted {len(rows)} sets")


def upsert_cards(cur, cards: list[dict]):
    rows = [
        (
            c["uuid"],
            c.get("externalId"),
            c["id"],
            c.get("externalUid"),
            c["name"],
            c.get("subtitle"),
            c.get("setCode"),
            c.get("cardNumber"),
            c.get("serialCode"),
            c.get("type"),
            c.get("type2"),
            c.get("rarity"),
            c.get("cost"),
            c.get("power"),
            c.get("hp"),
            c.get("arena"),
            c.get("aspects") or [],
            c.get("traits") or [],
            c.get("keywords") or [],
            c.get("frontText"),
            c.get("backText"),
            c.get("deployBox"),
            c.get("rules"),
            c.get("doubleSided", False),
            c.get("epicAction"),
            c.get("variantType"),
            c.get("isUnique", False),
            c.get("isLeader", False),
            c.get("isBase", False),
            c.get("frontImageUrl"),
            c.get("backImageUrl"),
            c.get("artist"),
            c.get("text"),
            c.get("additionalRulings") or [],
        )
        for c in cards
    ]
    psycopg2.extras.execute_values(
        cur,
        """
        INSERT INTO cards (
            uuid, external_id, card_id, external_uid,
            name, subtitle, set_code, card_number, serial_code,
            type, type2, rarity, cost, power, hp, arena,
            aspects, traits, keywords,
            front_text, back_text, deploy_box, rules,
            double_sided, epic_action, variant_type,
            is_unique, is_leader, is_base,
            front_image_url, back_image_url, artist,
            full_text, additional_rulings,
            synced_at
        ) VALUES %s
        ON CONFLICT (uuid) DO UPDATE SET
            external_id        = EXCLUDED.external_id,
            card_id            = EXCLUDED.card_id,
            name               = EXCLUDED.name,
            subtitle           = EXCLUDED.subtitle,
            set_code           = EXCLUDED.set_code,
            card_number        = EXCLUDED.card_number,
            serial_code        = EXCLUDED.serial_code,
            type               = EXCLUDED.type,
            type2              = EXCLUDED.type2,
            rarity             = EXCLUDED.rarity,
            cost               = EXCLUDED.cost,
            power              = EXCLUDED.power,
            hp                 = EXCLUDED.hp,
            arena              = EXCLUDED.arena,
            aspects            = EXCLUDED.aspects,
            traits             = EXCLUDED.traits,
            keywords           = EXCLUDED.keywords,
            front_text         = EXCLUDED.front_text,
            back_text          = EXCLUDED.back_text,
            deploy_box         = EXCLUDED.deploy_box,
            rules              = EXCLUDED.rules,
            double_sided       = EXCLUDED.double_sided,
            epic_action        = EXCLUDED.epic_action,
            variant_type       = EXCLUDED.variant_type,
            is_unique          = EXCLUDED.is_unique,
            is_leader          = EXCLUDED.is_leader,
            is_base            = EXCLUDED.is_base,
            front_image_url    = EXCLUDED.front_image_url,
            back_image_url     = EXCLUDED.back_image_url,
            artist             = EXCLUDED.artist,
            full_text          = EXCLUDED.full_text,
            additional_rulings = EXCLUDED.additional_rulings,
            synced_at          = now()
        """,
        rows,
        template="(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,now())",
    )
    log.info(f"  Upserted {len(rows)} cards")


def fetch_base_groups() -> list[dict]:
    log.info(f"Fetching {BASE_GROUPS_API} …")
    r = httpx.get(BASE_GROUPS_API, timeout=15)
    r.raise_for_status()
    groups = r.json()["base_groups"]
    log.info(f"  {len(groups)} base groups received")
    return groups


def upsert_base_groups(cur, groups: list[dict]):
    rows = [
        (
            g["uuid"],
            g["canonical_name"],
            g.get("color"),
            g.get("hp"),
            g.get("rarity_class"),
            g.get("created_at"),
            g.get("updated_at"),
        )
        for g in groups
    ]
    psycopg2.extras.execute_values(
        cur,
        """
        INSERT INTO base_groups (uuid, canonical_name, color, hp, rarity_class, created_at, updated_at)
        VALUES %s
        ON CONFLICT (uuid) DO UPDATE SET
            canonical_name = EXCLUDED.canonical_name,
            color          = EXCLUDED.color,
            hp             = EXCLUDED.hp,
            rarity_class   = EXCLUDED.rarity_class,
            updated_at     = EXCLUDED.updated_at
        """,
        rows,
    )
    log.info(f"  Upserted {len(rows)} base groups")


def main(wipe: bool = False):
    data        = fetch_export()
    base_groups = fetch_base_groups()

    conn = get_conn()
    conn.autocommit = False
    cur  = conn.cursor()

    if wipe:
        log.info("Dropping existing tables…")
        cur.execute(DROP_DDL)
        conn.commit()

    log.info("Creating tables and indexes…")
    cur.execute(DDL)
    conn.commit()

    log.info("Upserting sets…")
    upsert_sets(cur, data["sets"])
    conn.commit()

    log.info("Upserting cards…")
    upsert_cards(cur, data["cards"])
    conn.commit()

    log.info("Upserting base groups…")
    upsert_base_groups(cur, base_groups)
    conn.commit()

    # Summary
    cur.execute("SELECT COUNT(*) FROM sets")
    n_sets = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM cards")
    n_cards = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM cards WHERE variant_type = 'Standard'")
    n_std = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM cards WHERE is_leader")
    n_leaders = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM cards WHERE is_base")
    n_bases = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM base_groups")
    n_groups = cur.fetchone()[0]

    log.info(
        f"\nDone — {n_sets} sets, {n_cards} cards ({n_std} Standard, "
        f"{n_leaders} leaders, {n_bases} bases), {n_groups} base groups"
    )

    cur.close()
    conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Seed swuguru cards table from swuapi.com")
    parser.add_argument("--wipe", action="store_true", help="Drop and recreate tables before loading")
    args = parser.parse_args()
    main(wipe=args.wipe)
