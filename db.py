"""
db.py — Postgres connection pool
"""
import os
import psycopg2
from psycopg2 import pool
from dotenv import load_dotenv

load_dotenv()

_pool: pool.ThreadedConnectionPool | None = None


def get_pool() -> pool.ThreadedConnectionPool:
    global _pool
    if _pool is None:
        _pool = pool.ThreadedConnectionPool(
            minconn=1,
            maxconn=25,
            host=os.getenv("DB_HOST", "192.168.1.200"),
            port=int(os.getenv("DB_PORT", 5432)),
            dbname=os.getenv("DB_NAME", "swu_cards"),
            user=os.getenv("DB_USER", "swu_user"),
            password=os.getenv("DB_PASS", "changeme"),
        )
    return _pool


class DB:
    """Context manager: grabs a connection from the pool, auto-returns it."""

    def __enter__(self):
        self.conn = get_pool().getconn()
        self.conn.autocommit = False
        return self.conn

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type:
            self.conn.rollback()
        else:
            self.conn.commit()
        get_pool().putconn(self.conn)
        return False


def fetchall(sql: str, params=None) -> list[dict]:
    with DB() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]


def fetchone(sql: str, params=None) -> dict | None:
    rows = fetchall(sql, params)
    return rows[0] if rows else None


def execute(sql: str, params=None):
    with DB() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)


def execute_autocommit(sql: str, params=None):
    """
    Execute a statement outside any transaction block.
    Required for REFRESH MATERIALIZED VIEW CONCURRENTLY and
    CREATE DATABASE, which Postgres forbids inside transactions.
    """
    conn = get_pool().getconn()
    try:
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute(sql, params)
    finally:
        conn.autocommit = False
        get_pool().putconn(conn)