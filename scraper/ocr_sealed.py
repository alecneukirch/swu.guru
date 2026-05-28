"""
ocr_sealed.py — OCR sealed deck registration sheets via Claude Vision,
                 insert results into sealed_pools + sealed_pool_cards.

Usage:
    python3 ocr_sealed.py [--scans-dir ../scans] [--dry-run] [--pages 1-5] [--reprocess]
"""

import argparse
import base64
import json
import os
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "app" if (Path(__file__).parent.parent / "app").exists() else Path(__file__).parent.parent))

import anthropic

# ---------------------------------------------------------------------------
# Load env
# ---------------------------------------------------------------------------
_env_path = Path(__file__).parent.parent / ".env"
if _env_path.exists():
    for line in _env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())

# ---------------------------------------------------------------------------
# DB connection (reuse app db.py if available, else psycopg2 direct)
# ---------------------------------------------------------------------------
try:
    sys.path.insert(0, "/app")
    import db as _db
    def db_fetchall(sql, params=()):
        return _db.fetchall(sql, params)
    def db_execute(sql, params=()):
        return _db.execute(sql, params)
    def db_fetchone(sql, params=()):
        return _db.fetchone(sql, params)
    print("Using app/db.py")
except ImportError:
    import psycopg2
    import psycopg2.extras
    _conn = None
    def _get_conn():
        global _conn
        if _conn is None or _conn.closed:
            _conn = psycopg2.connect(
                host=os.environ["DB_HOST"],
                port=os.environ.get("DB_PORT", 5432),
                dbname=os.environ["DB_NAME"],
                user=os.environ["DB_USER"],
                password=os.environ["DB_PASS"],
            )
            _conn.autocommit = True
        return _conn
    def db_fetchall(sql, params=()):
        cur = _get_conn().cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(sql, params)
        return [dict(r) for r in cur.fetchall()]
    def db_execute(sql, params=()):
        cur = _get_conn().cursor()
        cur.execute(sql, params)
        return cur
    def db_fetchone(sql, params=()):
        rows = db_fetchall(sql, params)
        return rows[0] if rows else None
    print("Using direct psycopg2")

# ---------------------------------------------------------------------------
# Card reference data (from blank form)
# ---------------------------------------------------------------------------
LEADERS = {
    1:  "Saw Gerrera, Bring Down the Empire",
    2:  "Tobias Beckett, People are Predictable",
    3:  "Agent Kallus, Reconsider Your Allegiance",
    4:  "Aurra Sing, Assassin",
    5:  "Jyn Erso, Time to Fight",
    6:  "Vel Sartha, Aldhani Insurgent",
    7:  "Boba Fett, Krayt's Claw Commander",
    8:  "Director Krennic, Amidst My Achieve...",
    9:  "Hera Syndulla, Not Fighting Alone",
    10: "Leia Organa, Someone Who Loves You",
    11: "Darth Vader, Unstoppable",
    12: "Sebulba, Especially Dangerous Dug",
    13: "Chewbacca, Hero of Kessel",
    14: "Enfys Nest, Until We Can Go No Higher",
    15: "Jabba the Hutt, Crime Boss",
    16: "The Client, Please Lower Your Blaster",
    17: "Han Solo, I Got a Really Good Feeling",
    18: "Lando Calrissian, Full Sabacc",
}

BASES = [
    "Alliance Outpost (Blue)",
    "Daimyo's Palace (Blue)",
    "Coaxium Mine (Blue)",
    "Aldhani Garrison (Green)",
    "Great Pit of Carkoon (Green)",
    "Imperial Command Complex (Green)",
    "Contested Caverns (Red)",
    "Shipbreaking Yard (Red)",
    "Stygeon Spire (Red)",
    "Canto Bight (Yellow)",
    "Citadel Research Center (Yellow)",
    "Partisan Hideout (Yellow)",
]

VIGILANCE_BLUE = {
    102: "Choke on Aspirations",
    103: "Display Piece",
    99:  "Governor's Shuttle",
    100: "IGV-55 Listener",
    97:  "Imperial Door Technician",
    101: "Lawbringer, Shadow Over Lothal",
    98:  "Vandor Range Troopers",
    104: "Bodhi Rook, Creating a Diversion",
    105: "Cinta Kaz, Stone Cold and Fearless",
    106: "Defiant Scrapper",
    108: "Lando Calrissian, Eyes Open",
    111: "Leia's Disguise",
    110: "Phoenix Squadron Fighters",
    107: "Swoop Bike Marauder",
    109: "Tantive IV, Carrying Hope",
    126: "Adventurer Sniper Rifle",
    114: "Alkenzi Patroller",
    130: "Betrayed Trust",
    112: "Boonta Eve Flagbearer",
    121: "Canto Bight Security",
    117: "Conveyex Security Captain",
    118: "Droid Laser Turret",
    131: "Incapacitate",
    124: "Industrious Team",
    127: "Kill Switch",
    133: "Lost and Forgotten",
    129: "Mastery",
    115: "Rickety Quadjumper",
    116: "Rodian Bondsman",
    119: "Rogue One, At Any Cost",
    113: "Shield Drive Outfitter",
    122: "Shielded Hauler",
    123: "Syndicate Security",
    132: "The Tree Remembers",
    128: "Veiled Strength",
    120: "Vigilant Scouts",
    125: "Watchful",
}

COMMAND_GREEN = {
    139: "Admiral Motti, Chain of Command",
    134: "Bib Fortuna, Die Wanna Wanga?",
    140: "Intimidator, Citadel Overwatch",
    135: "Pirate Snub Fighter",
    137: "Ruthless Duo",
    136: "Syndicate Spice Runner",
    141: "Targeted For Removal",
    138: "Undercity Hunting Team",
    150: "Fulcrum",
    147: "Jaunty Light Freighter",
    143: "Liberated Wookiee",
    146: "Massassi Group Marines",
    144: "Phantom, Spectre Shuttle",
    145: "R2-D2, Part of the Plan",
    149: "Rey, Skywalker",
    142: "Scarif Lieutenant",
    148: "Smuggler's YT-2400",
    162: "Beach Patrol AT-ACT",
    152: "C-3PO, Translation Protocol",
    165: "Combat Exercise",
    167: "Common Cause",
    170: "Double-Cross",
    159: "Expendable Mercenary",
    153: "Follower of the Code",
    155: "Getaway Freighter",
    168: "Haymaker",
    160: "Hidden Hunters",
    156: "Hunter For Hire",
    158: "Khetanna, Upon the Dune Sea",
    164: "Mercenary Fleet",
    154: "Partisan Infantry",
    161: "Partisan U-Wing",
    169: "Payroll Heist",
    151: "Profiteering Hunter",
    166: "Putting a Team Together",
    171: "Stockpile",
    157: "Target Tagger",
    163: "The Sarlacc of Carkoon, Horror of th...",
}

AGGRESSION_RED = {
    174: "0-0-0, Translation and Torture",
    173: "BT-1, Blastomech",
    179: "Fear and Dead Men",
    178: "Persecutor, Fire Over Scarif",
    175: "Prototype TIE Advanced",
    176: "Sebulba's Podracer, Taking the Lead",
    177: "Son-tuul Berserkers",
    172: "Storm Raider",
    187: '"Staccato Lightning" Repeater',
    184: "Aerie, Cloud-Rider Dropship",
    183: "B-Wing Skirmisher",
    185: "Ben Solo, Facing the Light",
    181: "Cloud-Rider Veteran",
    186: "Enfys Nest's Helmet",
    180: "Inspired Recruit",
    182: "Weazel, Fighting Back",
    191: "Arvel Skeen, Win and Walk Away",
    207: "Attack From All Sides",
    192: "Bracca Shipbreaker",
    189: "Cavern Angels X-Wing",
    208: "Collateral Damage",
    202: "Commence the Festivities",
    203: "Daring Delve",
    194: "Doctor Aphra, Digging For Answers",
    198: "Dogged Pursuers",
    204: "Every Day, More Lies",
    205: "Flash the Vents",
    190: "Haxion Aggressor",
    193: "Mid Rim Sharpshooter",
    199: "Ohnaka Gang Bandits",
    195: "Overcharged Transport",
    196: "Relentless Hunters",
    200: "Salvaged Blaster",
    188: "Savareen Survivor",
    197: "Shifty Suspects",
    206: "That's a Rock",
    201: "Thermal Detonator",
}

CUNNING_YELLOW = {
    211: "Black Sun Patroller",
    214: "Boba Fett, For a Price",
    213: "Cutthroat Podracer",
    217: "Hold For Questioning",
    216: "Jabba's Rancor, Snack Time!",
    212: "Malakili, Keeper of the Menagerie",
    209: "Nihil Stormsower",
    210: "Salacious Crumb, Cackling Companion",
    215: "Vermillion, Qi'ra's Auction House",
    219: "Anakin's Podracer, So Wizard!",
    218: "Artful Pickpocket",
    225: "Han's Golden Dice",
    224: "Liberty, Draw Their Fire!",
    221: "Lieutenant Gorn, I Deserve Worse",
    222: "Rebel Blockade Runner",
    223: "Rose Tico, Now It's Worth It",
    226: "Secret Battle of Pretend",
    220: "Wookiee Guerilla",
    247: "Backed by the Hutts",
    236: "Bix Caleen, Selling Scrap",
    228: "Canyon Frontrunner",
    232: "Champion's KT9 Podracer",
    233: "Galen Erso, Destroying His Creation",
    239: "Guild Ambush Team",
    242: "Improvise",
    234: "Kage Elite",
    235: "Lady Proxima, Where's the Money?",
    240: "Milodon Rider",
    230: "Ohnaka Gang Starhopper",
    237: "Qui-Gon Jinn, Influencing Chance",
    227: "Rookie Rocket-Jumper",
    245: "Salvaged Materials",
    238: "Scavenging Sandcrawler",
    246: "The Axe Forgets",
    241: "The Blade Wing, The Secret of Shantip...",
    229: "The Master Codebreaker, High Stakes",
    243: "Transmission Jamming",
    244: "Unmarked Credits",
    231: "Weequay Pirate",
    248: "Windfall",
}

MULTICOLOR = {
    31: "Bossk, Join Our Merry Band",
    32: "Cad Bane, Now It's My Turn",
    33: "Hound's Tooth, Hunters' Approach",
    34: "Chewbacca, Mighty Rescuer",
    35: "Ezra Bridger, Spectre Six",
    36: "Obi-Wan Kenobi, Protector of Felucia",
    37: "Han Solo, Hibernation Sick",
    39: "Latts Razzi, Deadly Whipmaster",
    38: "Lepi Lookout",
    41: "Nothing Left to Fear",
    40: "Taramyn Barcona, Eyes Front!",
    42: "IG-88, Programmed to Kill",
    43: "Shadow Cloaking",
    44: "Single Reactor Ignition",
    47: "Baze Malbus, Good Luck",
    46: "Chirrut Imwe, I Don't Need Luck",
    45: "Zeb Orellios, Spectre Four",
    51: "Beilert Valance, Target: Vader",
    49: "Bith Brute",
    48: "Chio Fain, Four-Armed Slicer",
    50: "Honnah, OINK! SQUEE!",
    52: "The Mandalorian, Let's See the Puck",
    53: "Dengar, Take Your Shot",
    54: "Maul, Master of the Shadow Collective",
    56: "Cassian Andor, Everything For the Reb...",
    55: "Chopper, Spectre Three",
    61: "Asajj Ventress, Reluctant Hunter",
    57: "Benthic \"Two Tubes\", The War Has J...",
    62: "Defiant Hammerhead",
    59: "Highsinger, Deadly Droid",
    58: "Honor-Bound Partisan",
    63: "L3-37, Radical Instigator",
    60: "Quarren Contractor",
    65: "4-LOM, Devious",
    66: "Tear This Ship Apart",
    64: "Zuckuss, Dangerous",
    67: "Jyn Erso, Take the Next Chance",
    68: "Millennium Falcon, Dodging Patrols",
    69: "The Ghost, Home of the Spectres",
    70: "Devaronian Doorbuster",
    72: "Max Rebo, Encore!",
    74: "Maz Kanata, Where's My Boyfriend?",
    73: "Patient Hunter",
    71: "The Max Rebo Band, Jatz-Wailers",
    75: "Interrogation Droid",
    77: "Shadow of Stygeon Prime",
    76: "Vult Skerris's Defender, Secret Project",
    79: "K-2SO, Locking the Vault",
    80: "Luke Skywalker, Profit or Be Destroyed",
    78: "Sabine Wren, Spectre Five",
    83: "Broken Horn, Vizago's Pride",
    84: "Krrsantan, Hit and Run",
    81: "Sullustan Sapper",
    82: "Urrr'k, Elite Sharpshooter",
    85: "You Hold This",
    87: "Jango Fett, Wily Mercenary",
    86: "The Stranger, No Survivors",
    88: "Anakin Skywalker, Prescient Podracer",
    89: "Kanan Jarrus, Spectre One",
    95: "Finn, Looking Closer",
    94: "Hondo Ohnaka, Plays By His Own Rules",
    96: "Rhydonium Detonation",
    93: "Rio Durant, Beckett's Right Hands",
    90: "Toydarian Technician",
    92: "Two-Faced Troig",
    91: "Val, It's Been a Ride, Babe",
}

VILLAINY_BLACK = {
    249: "Black Sun Cabalist",
    250: "Callous Bounty Hunter",
    252: "Fett's Firespray, In Pursuit",
    251: "Night Wind Assailants",
}

HEROISM_WHITE = {
    253: "Alliance X-Wing",
    255: "Circuit Challenger",
    256: "Fire Across The Galaxy",
    254: "Stalwart Fleet Trooper",
}

NO_ASPECT_GRAY = {
    262: "Bank Job Fugitives",
    259: "Cartel Heavy Fighter",
    258: "Criminal Contact",
    264: "From a Certain Point of View",
    257: "Hidden Hand Supplier",
    263: "Kessel Hulk",
    260: "Seasoned Tracker",
    261: "Street Gang Recruiter",
}

ALL_SECTIONS = {
    "leader":     LEADERS,
    "vigilance":  VIGILANCE_BLUE,
    "command":    COMMAND_GREEN,
    "aggression": AGGRESSION_RED,
    "cunning":    CUNNING_YELLOW,
    "multicolor": MULTICOLOR,
    "villainy":   VILLAINY_BLACK,
    "heroism":    HEROISM_WHITE,
    "gray":       NO_ASPECT_GRAY,
}

# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = """You are an expert at reading handwritten forms. You are given a scanned Star Wars: Unlimited sealed deck registration sheet. Your task is to extract the data exactly as written.

The form has these sections on the FRONT side:
- Header: Table #, Player (First Name, Last Name, SWU ID), Verifier (First Name, Last Name, SWU ID), Event, Date
- LEADER section: each row has two handwritten boxes (PLAYED | TOTAL) then a pre-printed card name
- BASE section: each row has two handwritten boxes (PLAYED | TOTAL) then a pre-printed base name (no collector number)
- VIGILANCE (BLUE) section: two columns (PLAYED | TOTAL) then collector number then card name
- COMMAND (GREEN) section: two columns (PLAYED | TOTAL) then collector number then card name

IMPORTANT notes:
- PLAYED = copies in main deck (left column)
- TOTAL = copies in sealed pool (right column)
- Blank / empty boxes mean 0 (player has 0 copies)
- Numbers are typically 1, 2, or 3 — rarely higher
- Some fields may be blank if the player left them empty
- For the Base section, only 1 base is played but the pool may have multiple

Respond with ONLY valid JSON, no markdown, no explanation. Use this exact schema:
{
  "table_num": <int or null>,
  "player_first_name": <string or null>,
  "player_last_name": <string or null>,
  "player_swu_id": <string or null>,
  "verifier_first_name": <string or null>,
  "verifier_last_name": <string or null>,
  "verifier_swu_id": <string or null>,
  "ocr_notes": <string — note anything unclear, e.g. "table_num illegible", "played/total reversed?", "">,
  "sections": {
    "leader": [
      {"card_number": <int>, "card_name": <string>, "played": <int or null>, "total": <int or null>},
      ...
    ],
    "base": [
      {"card_name": <string>, "played": <int or null>, "total": <int or null>},
      ...
    ],
    "vigilance": [
      {"card_number": <int>, "card_name": <string>, "played": <int or null>, "total": <int or null>},
      ...
    ],
    "command": [
      {"card_number": <int>, "card_name": <string>, "played": <int or null>, "total": <int or null>},
      ...
    ]
  }
}

Only include rows where played > 0 OR total > 0. Skip rows where both are 0 or blank."""

def build_user_prompt(page_num: int) -> str:
    leader_list = "\n".join(f"  {n}. {name}" for n, name in sorted(LEADERS.items()))
    base_list   = "\n".join(f"  - {name}" for name in BASES)
    vig_list    = "\n".join(f"  {n}. {name}" for n, name in sorted(VIGILANCE_BLUE.items()))
    cmd_list    = "\n".join(f"  {n}. {name}" for n, name in sorted(COMMAND_GREEN.items()))
    return f"""This is page {page_num} of a sealed deck registration sheet.

Pre-printed card reference for this front side:

LEADER cards (by collector number):
{leader_list}

BASE options (no collector number):
{base_list}

VIGILANCE (BLUE) cards:
{vig_list}

COMMAND (GREEN) cards:
{cmd_list}

Please extract all data from this scan. Remember: PLAYED is the left column, TOTAL is the right column."""

# ---------------------------------------------------------------------------
# OCR one image
# ---------------------------------------------------------------------------
def _resize_image(image_path: Path, max_bytes: int = 4_500_000) -> tuple[bytes, str]:
    """Resize image to fit under max_bytes, return (bytes, media_type)."""
    from PIL import Image
    import io
    img = Image.open(image_path).convert("RGB")
    # Cap max dimension at 2400px — preserves readability for text/handwriting
    max_dim = 2400
    w, h = img.size
    if max(w, h) > max_dim:
        scale = max_dim / max(w, h)
        img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
    # Try progressively lower quality until it fits
    for quality in (85, 75, 65, 55):
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=quality)
        if buf.tell() <= max_bytes:
            return buf.getvalue(), "image/jpeg"
    # Last resort: scale down further
    w, h = img.size
    img = img.resize((int(w * 0.7), int(h * 0.7)), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=70)
    return buf.getvalue(), "image/jpeg"

def ocr_image(client: anthropic.Anthropic, image_path: Path, page_num: int) -> dict:
    img_bytes, media_type = _resize_image(image_path)
    b64 = base64.standard_b64encode(img_bytes).decode()

    resp = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=4096,
        system=SYSTEM_PROMPT,
        messages=[{
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": b64}},
                {"type": "text",  "text": build_user_prompt(page_num)},
            ]
        }]
    )

    raw = resp.content[0].text.strip()
    # Strip markdown code fences if present
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    return json.loads(raw)

# ---------------------------------------------------------------------------
# Insert to DB
# ---------------------------------------------------------------------------
def insert_pool(data: dict, page_num: int, dry_run: bool) -> int | None:
    if dry_run:
        print(f"  [dry-run] Would insert pool for page {page_num}: {data.get('player_first_name')} {data.get('player_last_name')}")
        return None

    row = db_fetchone(
        "INSERT INTO sealed_pools (scan_page, table_num, player_first_name, player_last_name, "
        "player_swu_id, verifier_first_name, verifier_last_name, verifier_swu_id, ocr_notes) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
        (
            page_num,
            data.get("table_num"),
            data.get("player_first_name"),
            data.get("player_last_name"),
            data.get("player_swu_id"),
            data.get("verifier_first_name"),
            data.get("verifier_last_name"),
            data.get("verifier_swu_id"),
            data.get("ocr_notes") or "",
        )
    )
    return row["id"]

def insert_cards(pool_id: int, sections: dict, dry_run: bool):
    rows = []
    for section_name, cards in sections.items():
        for card in cards:
            played = card.get("played") or 0
            total  = card.get("total")  or 0
            if played == 0 and total == 0:
                continue
            rows.append((
                pool_id,
                section_name,
                card.get("card_number"),
                card.get("card_name", ""),
                total,   # pool_count
                played,  # played_count
            ))

    if dry_run:
        print(f"  [dry-run] Would insert {len(rows)} card rows")
        return

    for r in rows:
        db_execute(
            "INSERT INTO sealed_pool_cards (pool_id, section, card_number, card_name, pool_count, played_count) "
            "VALUES (%s,%s,%s,%s,%s,%s)",
            r
        )

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--scans-dir", default=str(Path(__file__).parent.parent / "scans"))
    parser.add_argument("--dry-run",   action="store_true")
    parser.add_argument("--pages",     help="e.g. 1-10 or 5")
    parser.add_argument("--reprocess", action="store_true", help="Re-process pages already in DB")
    args = parser.parse_args()

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        sys.exit("ANTHROPIC_API_KEY not set")

    client = anthropic.Anthropic(api_key=api_key)
    scans_dir = Path(args.scans_dir)

    # Determine page range
    all_pages = sorted(
        int(p.stem.replace("scan-", ""))
        for p in scans_dir.glob("scan-*.png")
    )
    if args.pages:
        if "-" in args.pages:
            lo, hi = args.pages.split("-")
            all_pages = [p for p in all_pages if int(lo) <= p <= int(hi)]
        else:
            all_pages = [int(args.pages)]

    # Skip already-processed unless --reprocess
    if not args.reprocess and not args.dry_run:
        existing = {r["scan_page"] for r in db_fetchall("SELECT scan_page FROM sealed_pools")}
        skipped = [p for p in all_pages if p in existing]
        all_pages = [p for p in all_pages if p not in existing]
        if skipped:
            print(f"Skipping {len(skipped)} already-processed pages: {skipped}")

    print(f"Processing {len(all_pages)} pages: {all_pages}")

    for page_num in all_pages:
        img_path = scans_dir / f"scan-{page_num:02d}.png"
        if not img_path.exists():
            print(f"  Page {page_num}: file not found, skipping")
            continue

        print(f"\nPage {page_num}: {img_path.name} ...", end="", flush=True)
        try:
            data = ocr_image(client, img_path, page_num)
            print(f" OK — {data.get('player_first_name')} {data.get('player_last_name')} table={data.get('table_num')}")
            if data.get("ocr_notes"):
                print(f"  NOTE: {data['ocr_notes']}")

            pool_id = insert_pool(data, page_num, args.dry_run)
            if pool_id is not None:
                insert_cards(pool_id, data.get("sections", {}), args.dry_run)
                card_count = sum(len(v) for v in data.get("sections", {}).values())
                print(f"  Inserted pool_id={pool_id}, ~{card_count} card rows")

        except json.JSONDecodeError as e:
            print(f" JSON PARSE ERROR: {e}")
            print(f"  Raw response saved to /tmp/ocr_page_{page_num}_error.txt")
            Path(f"/tmp/ocr_page_{page_num}_error.txt").write_text(str(e))
        except Exception as e:
            print(f" ERROR: {e}")

        # Rate limit headroom
        time.sleep(0.5)

    print("\nDone.")

if __name__ == "__main__":
    main()
