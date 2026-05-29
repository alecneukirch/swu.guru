"""
ocr_sealed.py — OCR sealed deck registration sheets via Claude Vision,
                 insert results into sealed_pools + sealed_pool_cards.

PDF page layout: pages alternate front/back per pool.
  Page 1 = Pool 1 front, Page 2 = Pool 1 back,
  Page 3 = Pool 2 front, Page 4 = Pool 2 back, etc.

Usage:
    python3 ocr_sealed.py [--scans-dir ../scans] [--dry-run] [--pools 1-5] [--reprocess]
"""

import argparse
import base64
import json
import os
import re
import sys
import time
from pathlib import Path

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
# DB connection
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
# Card reference data
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
    187: "Staccato Lightning Repeater",
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
    57: "Benthic Two Tubes, The War Has J...",
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

# ---------------------------------------------------------------------------
# Prompts — front side
# ---------------------------------------------------------------------------
SYSTEM_PROMPT_FRONT = """You are an expert at reading handwritten forms. You are given a scanned Star Wars: Unlimited sealed deck registration sheet (FRONT SIDE).

The form has these sections:
- Header: Table #, Player (First Name, Last Name, SWU ID), Verifier (First Name, Last Name, SWU ID), Event, Date
- LEADER section: each row has two handwritten boxes (PLAYED | TOTAL) then a pre-printed card name
- BASE section: each row has two handwritten boxes (PLAYED | TOTAL) then a pre-printed base name
- VIGILANCE (BLUE) section: two columns (PLAYED | TOTAL) then collector number then card name
- COMMAND (GREEN) section: two columns (PLAYED | TOTAL) then collector number then card name

IMPORTANT notes:
- PLAYED = copies in main deck (left column)
- TOTAL = copies in sealed pool (right column)
- Blank / empty boxes mean 0
- Numbers are typically 1, 2, or 3
- Only 1 leader is played; only 1 base is played

Respond with ONLY valid JSON, no markdown, no explanation:
{
  "table_num": <int or null>,
  "player_first_name": <string or null>,
  "player_last_name": <string or null>,
  "player_swu_id": <string or null>,
  "verifier_first_name": <string or null>,
  "verifier_last_name": <string or null>,
  "verifier_swu_id": <string or null>,
  "ocr_notes": <string>,
  "sections": {
    "leader": [{"card_number": <int>, "card_name": <string>, "played": <int or null>, "total": <int or null>}, ...],
    "base":   [{"card_name": <string>, "played": <int or null>, "total": <int or null>}, ...],
    "vigilance": [{"card_number": <int>, "card_name": <string>, "played": <int or null>, "total": <int or null>}, ...],
    "command":   [{"card_number": <int>, "card_name": <string>, "played": <int or null>, "total": <int or null>}, ...]
  }
}

Only include rows where played > 0 OR total > 0."""


def build_front_prompt(pool_num: int) -> str:
    leader_list = "\n".join(f"  {n}. {name}" for n, name in sorted(LEADERS.items()))
    base_list   = "\n".join(f"  - {name}" for name in BASES)
    vig_list    = "\n".join(f"  {n}. {name}" for n, name in sorted(VIGILANCE_BLUE.items()))
    cmd_list    = "\n".join(f"  {n}. {name}" for n, name in sorted(COMMAND_GREEN.items()))
    return f"""This is the FRONT side of pool #{pool_num}.

LEADER cards:
{leader_list}

BASE options:
{base_list}

VIGILANCE (BLUE) cards:
{vig_list}

COMMAND (GREEN) cards:
{cmd_list}

Extract all data. PLAYED is the left column, TOTAL is the right column."""


# ---------------------------------------------------------------------------
# Prompts — back side
# ---------------------------------------------------------------------------
SYSTEM_PROMPT_BACK = """You are an expert at reading handwritten forms. You are given a scanned Star Wars: Unlimited sealed deck registration sheet (BACK SIDE).

The back side has these sections only (no header info):
- AGGRESSION (RED) section: two columns (PLAYED | TOTAL) then collector number then card name
- CUNNING (YELLOW) section: two columns (PLAYED | TOTAL) then collector number then card name
- MULTICOLOR section: two columns (PLAYED | TOTAL) then collector number then card name
- VILLAINY (BLACK) section: two columns (PLAYED | TOTAL) then collector number then card name
- HEROISM (WHITE) section: two columns (PLAYED | TOTAL) then collector number then card name
- NO ASPECT (GRAY) section: two columns (PLAYED | TOTAL) then collector number then card name

IMPORTANT notes:
- PLAYED = copies in main deck (left column)
- TOTAL = copies in sealed pool (right column)
- Blank / empty boxes mean 0
- Numbers are typically 1, 2, or 3

Respond with ONLY valid JSON, no markdown, no explanation:
{
  "ocr_notes": <string>,
  "sections": {
    "aggression": [{"card_number": <int>, "card_name": <string>, "played": <int or null>, "total": <int or null>}, ...],
    "cunning":    [{"card_number": <int>, "card_name": <string>, "played": <int or null>, "total": <int or null>}, ...],
    "multicolor": [{"card_number": <int>, "card_name": <string>, "played": <int or null>, "total": <int or null>}, ...],
    "villainy":   [{"card_number": <int>, "card_name": <string>, "played": <int or null>, "total": <int or null>}, ...],
    "heroism":    [{"card_number": <int>, "card_name": <string>, "played": <int or null>, "total": <int or null>}, ...],
    "gray":       [{"card_number": <int>, "card_name": <string>, "played": <int or null>, "total": <int or null>}, ...]
  }
}

Only include rows where played > 0 OR total > 0."""


def build_back_prompt(pool_num: int) -> str:
    agg_list  = "\n".join(f"  {n}. {name}" for n, name in sorted(AGGRESSION_RED.items()))
    cun_list  = "\n".join(f"  {n}. {name}" for n, name in sorted(CUNNING_YELLOW.items()))
    mul_list  = "\n".join(f"  {n}. {name}" for n, name in sorted(MULTICOLOR.items()))
    vil_list  = "\n".join(f"  {n}. {name}" for n, name in sorted(VILLAINY_BLACK.items()))
    her_list  = "\n".join(f"  {n}. {name}" for n, name in sorted(HEROISM_WHITE.items()))
    gray_list = "\n".join(f"  {n}. {name}" for n, name in sorted(NO_ASPECT_GRAY.items()))
    return f"""This is the BACK side of pool #{pool_num}.

AGGRESSION (RED) cards:
{agg_list}

CUNNING (YELLOW) cards:
{cun_list}

MULTICOLOR cards:
{mul_list}

VILLAINY (BLACK) cards:
{vil_list}

HEROISM (WHITE) cards:
{her_list}

NO ASPECT (GRAY) cards:
{gray_list}

Extract all data. PLAYED is the left column, TOTAL is the right column."""


# ---------------------------------------------------------------------------
# Image helpers
# ---------------------------------------------------------------------------
def _resize_image(image_path: Path, max_bytes: int = 4_500_000) -> tuple[bytes, str]:
    from PIL import Image
    import io
    img = Image.open(image_path).convert("RGB")
    max_dim = 2400
    w, h = img.size
    if max(w, h) > max_dim:
        scale = max_dim / max(w, h)
        img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
    for quality in (85, 75, 65, 55):
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=quality)
        if buf.tell() <= max_bytes:
            return buf.getvalue(), "image/jpeg"
    w, h = img.size
    img = img.resize((int(w * 0.7), int(h * 0.7)), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=70)
    return buf.getvalue(), "image/jpeg"


def _fix_json(raw: str) -> str:
    """Best-effort repair of common Claude JSON issues."""
    # Strip code fences
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    raw = raw.strip()
    # Remove trailing commas before } or ]
    raw = re.sub(r",\s*([}\]])", r"\1", raw)
    # Truncate at last complete top-level object
    depth = 0
    last_close = -1
    for i, ch in enumerate(raw):
        if ch == '{':
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0:
                last_close = i
                break
    if last_close > 0:
        raw = raw[:last_close + 1]
    return raw


def ocr_image(client: anthropic.Anthropic, image_path: Path, system: str, user: str, retries: int = 3) -> dict:
    img_bytes, media_type = _resize_image(image_path)
    b64 = base64.standard_b64encode(img_bytes).decode()
    last_err = None
    for attempt in range(retries):
        if attempt > 0:
            time.sleep(2)
        resp = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=8192,
            system=system,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": b64}},
                    {"type": "text",  "text": user},
                ]
            }]
        )
        raw = resp.content[0].text.strip()
        try:
            raw = _fix_json(raw)
            return json.loads(raw)
        except json.JSONDecodeError as e:
            last_err = e
            if attempt < retries - 1:
                print(f" (retry {attempt+1})", end="", flush=True)
    raise last_err


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------
def insert_pool(data: dict, front_page: int, dry_run: bool) -> int | None:
    if dry_run:
        print(f"  [dry-run] Would insert pool for page {front_page}: {data.get('player_first_name')} {data.get('player_last_name')}")
        return None
    row = db_fetchone(
        "INSERT INTO sealed_pools (scan_page, table_num, player_first_name, player_last_name, "
        "player_swu_id, verifier_first_name, verifier_last_name, verifier_swu_id, ocr_notes) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
        (
            front_page,
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


def insert_cards(pool_id: int, sections: dict, dry_run: bool) -> int:
    rows = []
    for section_name, cards in sections.items():
        for card in cards:
            played = card.get("played") or 0
            total  = card.get("total")  or 0
            if played == 0 and total == 0:
                continue
            rows.append((pool_id, section_name, card.get("card_number"), card.get("card_name", ""), total, played))
    if dry_run:
        print(f"  [dry-run] Would insert {len(rows)} card rows")
        return len(rows)
    for r in rows:
        db_execute(
            "INSERT INTO sealed_pool_cards (pool_id, section, card_number, card_name, pool_count, played_count) "
            "VALUES (%s,%s,%s,%s,%s,%s)",
            r
        )
    return len(rows)


def append_back_notes(pool_id: int, notes: str, dry_run: bool):
    if dry_run or not notes:
        return
    db_execute(
        "UPDATE sealed_pools SET ocr_notes = ocr_notes || %s WHERE id = %s",
        (f" | BACK: {notes}", pool_id)
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--scans-dir",  default=str(Path(__file__).parent.parent / "scans"))
    parser.add_argument("--dry-run",    action="store_true")
    parser.add_argument("--pools",      help="Pool range to process, e.g. 1-10 or 5")
    parser.add_argument("--reprocess",  action="store_true")
    parser.add_argument("--back-only",  action="store_true",
                        help="Only process back sides for pools missing back-side sections")
    args = parser.parse_args()

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        sys.exit("ANTHROPIC_API_KEY not set")

    client = anthropic.Anthropic(api_key=api_key)
    scans_dir = Path(args.scans_dir)

    # Pool numbers 1..45; front page = 2*pool-1, back page = 2*pool
    all_pools = list(range(1, 46))
    if args.pools:
        if "-" in args.pools:
            lo, hi = args.pools.split("-")
            all_pools = [p for p in all_pools if int(lo) <= p <= int(hi)]
        else:
            all_pools = [int(args.pools)]

    BACK_SECTIONS = {"aggression", "cunning", "multicolor", "villainy", "heroism", "gray"}

    if args.back_only:
        # Find pools that exist but have no back-side card rows
        rows = db_fetchall("""
            SELECT sp.id, sp.scan_page
            FROM sealed_pools sp
            WHERE NOT EXISTS (
                SELECT 1 FROM sealed_pool_cards spc
                WHERE spc.pool_id = sp.id
                AND spc.section = ANY(ARRAY['aggression','cunning','multicolor','villainy','heroism','gray'])
            )
            ORDER BY sp.scan_page
        """)
        # Map scan_page (front page) → pool_num
        back_only_pools = []
        for r in rows:
            front_page = r["scan_page"]
            # front_page should be odd: 1,3,5,...
            pool_num = (front_page + 1) // 2
            if pool_num in all_pools:
                back_only_pools.append((pool_num, r["id"], front_page))
        print(f"Back-only mode: {len(back_only_pools)} pools need back-side data")
        for pool_num, pool_id, front_page in back_only_pools:
            back_page = front_page + 1
            back_img  = scans_dir / f"scan-{back_page:02d}.png"
            if not back_img.exists():
                print(f"  Pool {pool_num}: back image {back_img.name} not found, skipping")
                continue
            print(f"\nPool {pool_num} (back page {back_page}): ...", end="", flush=True)
            try:
                back_data = ocr_image(client, back_img, SYSTEM_PROMPT_BACK, build_back_prompt(pool_num))
                back_sections = back_data.get("sections", {})
                back_card_count = sum(len(v) for v in back_sections.values())
                print(f" OK — ~{back_card_count} card rows")
                if back_data.get("ocr_notes"):
                    print(f"  BACK NOTE: {back_data['ocr_notes']}")
                if not args.dry_run:
                    insert_cards(pool_id, back_sections, args.dry_run)
                    append_back_notes(pool_id, back_data.get("ocr_notes", ""), args.dry_run)
            except json.JSONDecodeError as e:
                print(f" JSON PARSE ERROR: {e}")
                Path(f"/tmp/ocr_pool_{pool_num}_back_error.txt").write_text(str(e))
            except Exception as e:
                print(f" ERROR: {e}")
            time.sleep(0.5)
        print("\nDone.")
        return

    # Skip already-processed front pages unless --reprocess
    if not args.reprocess and not args.dry_run:
        existing_front_pages = {r["scan_page"] for r in db_fetchall("SELECT scan_page FROM sealed_pools")}
        skipped = [p for p in all_pools if (2 * p - 1) in existing_front_pages]
        all_pools = [p for p in all_pools if (2 * p - 1) not in existing_front_pages]
        if skipped:
            print(f"Skipping {len(skipped)} already-processed pools: {skipped}")

    print(f"Processing {len(all_pools)} pools: {all_pools}")

    for pool_num in all_pools:
        front_page = 2 * pool_num - 1
        back_page  = 2 * pool_num
        front_img  = scans_dir / f"scan-{front_page:02d}.png"
        back_img   = scans_dir / f"scan-{back_page:02d}.png"

        if not front_img.exists():
            print(f"\nPool {pool_num}: front image {front_img.name} not found, skipping")
            continue

        print(f"\nPool {pool_num} (pages {front_page}/{back_page}): front ...", end="", flush=True)
        try:
            front_data = ocr_image(client, front_img, SYSTEM_PROMPT_FRONT, build_front_prompt(pool_num))
            print(f" OK — {front_data.get('player_first_name')} {front_data.get('player_last_name')} table={front_data.get('table_num')}")
            if front_data.get("ocr_notes"):
                print(f"  FRONT NOTE: {front_data['ocr_notes']}")

            pool_id = insert_pool(front_data, front_page, args.dry_run)
            card_count = 0
            if pool_id is not None:
                card_count += insert_cards(pool_id, front_data.get("sections", {}), args.dry_run)

        except json.JSONDecodeError as e:
            print(f" JSON PARSE ERROR: {e}")
            Path(f"/tmp/ocr_pool_{pool_num}_front_error.txt").write_text(str(e))
            continue
        except Exception as e:
            print(f" ERROR: {e}")
            continue

        time.sleep(0.5)

        # Back side
        if not back_img.exists():
            print(f"  back image {back_img.name} not found, skipping back side")
        else:
            print(f"  back ...", end="", flush=True)
            try:
                back_data = ocr_image(client, back_img, SYSTEM_PROMPT_BACK, build_back_prompt(pool_num))
                back_sections = back_data.get("sections", {})
                back_card_count = sum(len(v) for v in back_sections.values())
                print(f" OK — ~{back_card_count} card rows")
                if back_data.get("ocr_notes"):
                    print(f"  BACK NOTE: {back_data['ocr_notes']}")

                if pool_id is not None:
                    card_count += insert_cards(pool_id, back_sections, args.dry_run)
                    append_back_notes(pool_id, back_data.get("ocr_notes", ""), args.dry_run)

            except json.JSONDecodeError as e:
                print(f" JSON PARSE ERROR: {e}")
                Path(f"/tmp/ocr_pool_{pool_num}_back_error.txt").write_text(str(e))
            except Exception as e:
                print(f" ERROR: {e}")

            time.sleep(0.5)

        print(f"  pool_id={pool_id}, ~{card_count} total card rows")

    print("\nDone.")


if __name__ == "__main__":
    main()
