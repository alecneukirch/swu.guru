-- migrate_swustats.sql
-- Tables for syncing weekly meta statistics from swustats.net

CREATE TABLE IF NOT EXISTS swustats_matchup_stats (
    id                        SERIAL PRIMARY KEY,
    week_num                  INTEGER NOT NULL,
    leader_id                 TEXT    NOT NULL,
    base_id                   TEXT    NOT NULL,
    opponent_leader_id        TEXT    NOT NULL,
    opponent_base_id          TEXT    NOT NULL,
    num_wins                  INTEGER NOT NULL DEFAULT 0,
    num_plays                 INTEGER NOT NULL DEFAULT 0,
    plays_going_first         INTEGER NOT NULL DEFAULT 0,
    turns_in_wins             INTEGER NOT NULL DEFAULT 0,
    total_turns               INTEGER NOT NULL DEFAULT 0,
    cards_resourced_in_wins   INTEGER NOT NULL DEFAULT 0,
    total_cards_resourced     INTEGER NOT NULL DEFAULT 0,
    remaining_health_in_wins  INTEGER NOT NULL DEFAULT 0,
    wins_going_first          INTEGER NOT NULL DEFAULT 0,
    wins_going_second         INTEGER NOT NULL DEFAULT 0,
    synced_at                 TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (week_num, leader_id, base_id, opponent_leader_id, opponent_base_id)
);

CREATE INDEX IF NOT EXISTS idx_swustats_matchup_week
    ON swustats_matchup_stats (week_num);

CREATE INDEX IF NOT EXISTS idx_swustats_matchup_deck
    ON swustats_matchup_stats (week_num, leader_id, base_id);

-- swustats_card_stats: per-deck-archetype card usage stats.
-- Join to swustats_matchup_stats on (week_num, leader_id, base_id)
-- to get matchup context for any deck.
CREATE TABLE IF NOT EXISTS swustats_card_stats (
    id                         SERIAL PRIMARY KEY,
    week_num                   INTEGER NOT NULL,
    leader_id                  TEXT    NOT NULL,
    base_id                    TEXT    NOT NULL,
    card_uid                   TEXT    NOT NULL,
    card_name                  TEXT,
    times_included             INTEGER     NOT NULL DEFAULT 0,
    times_included_in_wins     INTEGER     NOT NULL DEFAULT 0,
    percent_included_in_wins   NUMERIC(6,2),
    times_played               INTEGER     NOT NULL DEFAULT 0,
    times_played_in_wins       INTEGER     NOT NULL DEFAULT 0,
    percent_played_in_wins     NUMERIC(6,2),
    times_resourced            INTEGER     NOT NULL DEFAULT 0,
    times_resourced_in_wins    INTEGER     NOT NULL DEFAULT 0,
    percent_resourced_in_wins  NUMERIC(6,2),
    synced_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (week_num, leader_id, base_id, card_uid)
);

CREATE INDEX IF NOT EXISTS idx_swustats_card_deck
    ON swustats_card_stats (week_num, leader_id, base_id);

CREATE INDEX IF NOT EXISTS idx_swustats_card_uid
    ON swustats_card_stats (week_num, card_uid);

GRANT ALL ON swustats_matchup_stats, swustats_card_stats TO swu_user;
GRANT USAGE, SELECT ON SEQUENCE swustats_matchup_stats_id_seq, swustats_card_stats_id_seq TO swu_user;

CREATE OR REPLACE VIEW swustats_matchup_view AS
SELECT
    m.week_num,
    m.leader_id,
    lc.card_name                      AS leader_name,
    m.base_id,
    bc.card_name                      AS base_name,
    m.opponent_leader_id,
    olc.card_name                     AS opponent_leader_name,
    m.opponent_base_id,
    m.num_wins,
    m.num_plays,
    m.plays_going_first,
    m.turns_in_wins,
    m.total_turns,
    m.cards_resourced_in_wins,
    m.total_cards_resourced,
    m.remaining_health_in_wins,
    m.wins_going_first,
    m.wins_going_second,
    m.synced_at
FROM swustats_matchup_stats m
LEFT JOIN swustats_card_stats lc  ON lc.card_uid = m.leader_id           AND lc.week_num = 0
LEFT JOIN swustats_card_stats bc  ON bc.card_uid = m.base_id             AND bc.week_num = 0
LEFT JOIN swustats_card_stats olc ON olc.card_uid = m.opponent_leader_id AND olc.week_num = 0;

GRANT SELECT ON swustats_matchup_view TO swu_user;
