-- Sealed/Limited pool data from paper deck registration sheets
-- Front-side sections: Leader, Base, Vigilance (Blue), Command (Green)
-- Back-side sections: Aggression (Red), Cunning (Yellow), Multicolor,
--                     Villainy (Black), Heroism (White), No Aspect (Gray)

CREATE TABLE IF NOT EXISTS sealed_pools (
    id                    SERIAL PRIMARY KEY,
    event_id              INT,  -- FK to events(id), nullable (event may not be in DB yet)
    scan_page             INT NOT NULL,           -- which page in the source PDF (1-based)
    table_num             INT,
    player_first_name     TEXT,
    player_last_name      TEXT,
    player_swu_id         TEXT,
    verifier_first_name   TEXT,
    verifier_last_name    TEXT,
    verifier_swu_id       TEXT,
    front_scanned         BOOLEAN NOT NULL DEFAULT TRUE,
    back_scanned          BOOLEAN NOT NULL DEFAULT FALSE,
    notes                 TEXT,
    ocr_notes             TEXT,                   -- any extraction warnings
    created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS sealed_pool_cards (
    id           SERIAL PRIMARY KEY,
    pool_id      INT NOT NULL REFERENCES sealed_pools(id) ON DELETE CASCADE,
    section      TEXT NOT NULL,   -- 'leader','base','vigilance','command','aggression',
                                  --  'cunning','multicolor','villainy','heroism','gray'
    card_number  INT,             -- set collector number (NULL for bases)
    card_name    TEXT NOT NULL,   -- full name as printed on form
    pool_count   INT,             -- TOTAL column: copies in sealed pool
    played_count INT              -- PLAYED column: copies in main deck
);

CREATE INDEX IF NOT EXISTS sealed_pool_cards_pool_id ON sealed_pool_cards(pool_id);
CREATE INDEX IF NOT EXISTS sealed_pools_event_id     ON sealed_pools(event_id);
