"""
scraper/bootstrap_players.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
One-time bootstrap: creates a player_identity for every unique melee_player_id
in the standings table, then auto-suggests merges for same-name players who
never shared a tournament.

Run after migrate_006.sql:
    python -m scraper.bootstrap_players
"""
import sys, uuid, logging
sys.path.insert(0, '.')
import db

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

def bootstrap():
    # 1. Get all distinct melee_player_ids with their most recent display name
    log.info("Loading player data...")
    rows = db.fetchall("""
        SELECT DISTINCT ON (s.melee_player_id)
            s.melee_player_id,
            s.player_name
        FROM standings s
        JOIN events e ON e.id = s.event_id
        WHERE s.melee_player_id IS NOT NULL
          AND s.player_name IS NOT NULL AND s.player_name != ''
        ORDER BY s.melee_player_id, e.date DESC
    """)
    log.info(f"Found {len(rows)} unique melee player IDs")

    # 2. Group by normalized display name to find potential merges
    from collections import defaultdict
    by_name = defaultdict(list)
    for r in rows:
        by_name[r['player_name'].strip().lower()].append(r)

    # 3. For each name group, check for tournament overlap
    log.info("Checking for same-name players...")
    
    # Get tournament participation per melee_player_id
    tourney_rows = db.fetchall("""
        SELECT melee_player_id, ARRAY_AGG(DISTINCT event_id) AS event_ids
        FROM standings
        WHERE melee_player_id IS NOT NULL
        GROUP BY melee_player_id
    """)
    tourney_map = {r['melee_player_id']: set(r['event_ids']) for r in tourney_rows}

    # 4. Build identity assignments
    # identity_id -> list of melee_player_ids
    assignments = {}  # melee_player_id -> identity_id
    
    for name, group in by_name.items():
        if len(group) == 1:
            # Unique name — create own identity
            identity_id = str(uuid.uuid4())
            assignments[group[0]['melee_player_id']] = {
                'identity_id': identity_id,
                'display_name': group[0]['player_name'],
                'confidence': 'manual',
                'status': 'confirmed',
            }
        else:
            # Multiple IDs with same name — check overlap
            # Build clusters: IDs that can be merged (no shared tournaments)
            clusters = []
            for player in group:
                pid = player['melee_player_id']
                events = tourney_map.get(pid, set())
                placed = False
                for cluster in clusters:
                    # Check overlap with all existing members
                    overlap = any(
                        events & tourney_map.get(existing_pid, set())
                        for existing_pid in cluster['members']
                    )
                    if not overlap:
                        cluster['members'].append(pid)
                        placed = True
                        break
                if not placed:
                    clusters.append({'members': [pid], 'name': player['player_name']})

            for cluster in clusters:
                identity_id = str(uuid.uuid4())
                confidence = 'auto_high' if len(cluster['members']) > 1 else 'manual'
                status     = 'confirmed' if confidence == 'auto_high' else 'confirmed'
                for pid in cluster['members']:
                    pname = next(r['player_name'] for r in group if r['melee_player_id'] == pid)
                    assignments[pid] = {
                        'identity_id': identity_id,
                        'display_name': pname,
                        'confidence': confidence,
                        'status': status,
                    }

    # 5. Insert identities
    log.info("Inserting identities...")
    seen_identities = set()
    identity_rows = []
    for pid, a in assignments.items():
        iid = a['identity_id']
        if iid not in seen_identities:
            seen_identities.add(iid)
            identity_rows.append((iid, a['display_name']))

    for iid, name in identity_rows:
        db.execute(
            "INSERT INTO player_identities (id, display_name) VALUES (%s, %s) ON CONFLICT DO NOTHING",
            (iid, name)
        )

    # 6. Insert mappings
    log.info("Inserting player ID mappings...")
    merged = 0
    for pid, a in assignments.items():
        db.execute("""
            INSERT INTO player_id_map (melee_player_id, identity_id, display_name, confidence, status)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (melee_player_id) DO UPDATE
                SET identity_id = EXCLUDED.identity_id,
                    display_name = EXCLUDED.display_name,
                    confidence = EXCLUDED.confidence,
                    status = EXCLUDED.status
        """, (pid, a['identity_id'], a['display_name'], a['confidence'], a['status']))
        if a['confidence'] == 'auto_high':
            merged += 1

    total_identities = len(seen_identities)
    log.info(f"Done. {len(assignments)} melee IDs → {total_identities} identities")
    log.info(f"Auto-merged: {merged} IDs under shared identities")

if __name__ == '__main__':
    bootstrap()
