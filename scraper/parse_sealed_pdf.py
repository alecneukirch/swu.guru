"""
scraper/parse_sealed_pdf.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~
OCR pipeline for SWU Planetary Qualifier sealed pool decklists.

Reads sealedPQLists.pdf (2 pages per pool), renders each pair as images,
sends them to the Claude vision API for structured extraction, and writes
results to the sealed_pools and sealed_pool_cards tables.

Usage:
    python -m scraper.parse_sealed_pdf                  # all unprocessed pools
    python -m scraper.parse_sealed_pdf --pool 3         # pool #3 only (1-based)
    python -m scraper.parse_sealed_pdf --pool 1-10      # pools 1 through 10
    python -m scraper.parse_sealed_pdf --dry-run        # print extracted data, no DB writes
    python -m scraper.parse_sealed_pdf --reprocess 5    # reprocess pool 5 even if in DB
"""

import argparse
import base64
import json
import sys
import time
from pathlib import Path

import fitz  # PyMuPDF
import anthropic

sys.path.insert(0, str(Path(__file__).parent.parent))
import db

PDF_PATH = Path(__file__).parent.parent / "sealedPQLists.pdf"
MODEL_POOL   = "claude-sonnet-4-6"
MODEL_PLAYED = "claude-opus-4-7"
DPI_SCALE    = 5.0  # ~360 dpi

# PDF point coordinates (pt) of the TOTAL (pool) box column per section.
# Each section has [PLAYED box][TOTAL box][card no.][card name].
# We white out only the TOTAL box strips on the played-detection render
# so the model can't confuse the two adjacent columns.
# Coordinates measured by direct pixel-level border detection on sealedPQLists.pdf at DPI_SCALE=5.
# Front page sections: LEADER (0-203.6pt), VIGILANCE (203.6-428.7pt), COMMAND (428.7-612pt).
# Back page sections: AGG/VIL/HER (0-235pt), CUNNING/GRAY (235-401pt), MULTICOLOR (401-612pt).
# Within each section layout: [PLAYED box][TOTAL box][NO.#][CARD NAME].
# TOTAL box ranges measured from header-row scans on sealedPQLists.pdf at DPI_SCALE=5:
#   Front: LEADER=28.5-51.9pt, VIGILANCE≈216-242pt, COMMAND=449-467pt
#   Back:  sect1≈28-46pt, sect2=255.6-273.4pt, sect3=424.8-445.4pt
_FRONT_TOTAL_STRIPS_PT = [(28, 53), (216, 242), (448, 468)]   # leader, vigilance, command
_BACK_TOTAL_STRIPS_PT  = [(22, 47), (255, 274), (424, 446)]   # aggression+vil+her, cunning+gray, multicolor

# ── Complete card list from the digital blank template ──────────────────────

LEADERS = [
    (1,  "Saw Gerrera, Bring Down the Empire"),
    (2,  "Tobias Beckett, People are Predictable"),
    (3,  "Agent Kallus, Reconsider Your Allegiance"),
    (4,  "Aurra Sing, Assassin"),
    (5,  "Jyn Erso, Time to Fight"),
    (6,  "Vel Sartha, Aldhani Insurgent"),
    (7,  "Boba Fett, Krayt's Claw Commander"),
    (8,  "Director Krennic, Amidst My Achievement"),
    (9,  "Hera Syndulla, Not Fighting Alone"),
    (10, "Leia Organa, Someone Who Loves You"),
    (11, "Darth Vader, Unstoppable"),
    (12, "Sebulba, Especially Dangerous Dug"),
    (13, "Chewbacca, Hero of Kessel"),
    (14, "Enfys Nest, Until We Can Go No Higher"),
    (15, "Jabba the Hutt, Crime Boss"),
    (16, "The Client, Please Lower Your Blaster"),
    (17, "Han Solo, I Got a Really Good Feeling"),
    (18, "Lando Calrissian, Full Sabacc"),
]

BASES = [
    "Alliance Outpost (Blue)",
    "Daimyo's Palace (Blue)",
    "Coaxium Mine (Blue)",
    "Aldhani Garrison (Green)",
    "Great Pit of Carkoon (Green)",
    "Imperial Commend Complex (Green)",
    "Contested Caverns (Red)",
    "Shipbreaking Yard (Red)",
    "Stygeon Spire (Red)",
    "Canto Bight (Yellow)",
    "Citadel Research Center (Yellow)",
    "Partisan Hideout (Yellow)",
]

VIGILANCE = [
    (97,  "Imperial Door Technician"),
    (98,  "Vandor Range Troopers"),
    (99,  "Governor's Shuttle"),
    (100, "IGV-55 Listener"),
    (101, "Lawbringer, Shadow Over Lothal"),
    (102, "Choke on Aspirations"),
    (103, "Display Piece"),
    (104, "Bodhi Rook, Creating a Diversion"),
    (105, "Cinta Kaz, Stone Cold and Fearless"),
    (106, "Defiant Scrapper"),
    (107, "Swoop Bike Marauder"),
    (108, "Lando Calrissian, Eyes Open"),
    (109, "Tantive IV, Carrying Hope"),
    (110, "Phoenix Squadron Fighters"),
    (111, "Leia's Disguise"),
    (112, "Boonta Eve Flagbearer"),
    (113, "Shield Drive Outfitter"),
    (114, "Alkenzi Patroller"),
    (115, "Rickety Quadjumper"),
    (116, "Rodian Bondsman"),
    (117, "Conveyex Security Captain"),
    (118, "Droid Laser Turret"),
    (119, "Rogue One, At Any Cost"),
    (120, "Vigilant Scouts"),
    (121, "Canto Bight Security"),
    (122, "Shielded Hauler"),
    (123, "Syndicate Security"),
    (124, "Industrious Team"),
    (125, "Watchful"),
    (126, "Adventurer Sniper Rifle"),
    (127, "Kill Switch"),
    (128, "Veiled Strength"),
    (129, "Mastery"),
    (130, "Betrayed Trust"),
    (131, "Incapacitate"),
    (132, "The Tree Remembers"),
    (133, "Lost and Forgotten"),
]

COMMAND = [
    (134, "Bib Fortuna, Die Wanna Wanga?"),
    (135, "Pirate Snub Fighter"),
    (136, "Syndicate Spice Runner"),
    (137, "Ruthless Duo"),
    (138, "Undercity Hunting Team"),
    (139, "Admiral Motti, Chain of Command"),
    (140, "Intimidator, Citadel Overwatch"),
    (141, "Targeted For Removal"),
    (142, "Scarif Lieutenant"),
    (143, "Liberated Wookiee"),
    (144, "Phantom, Spectre Shuttle"),
    (145, "R2-D2, Part of the Plan"),
    (146, "Massassi Group Marines"),
    (147, "Jaunty Light Freighter"),
    (148, "Smuggler's YT-2400"),
    (149, "Rey, Skywalker"),
    (150, "Fulcrum"),
    (151, "Profiteering Hunter"),
    (152, "C-3PO, Translation Protocol"),
    (153, "Follower of the Code"),
    (154, "Partisan Infantry"),
    (155, "Getaway Freighter"),
    (156, "Hunter For Hire"),
    (157, "Target Tagger"),
    (158, "Khetanna, Upon the Dune Sea"),
    (159, "Expendable Mercenary"),
    (160, "Hidden Hunters"),
    (161, "Partisan U-Wing"),
    (162, "Beach Patrol AT-ACT"),
    (163, "The Sarlacc of Carkoon, Horror of the Desert"),
    (164, "Mercenary Fleet"),
    (165, "Combat Exercise"),
    (166, "Putting a Team Together"),
    (167, "Common Cause"),
    (168, "Haymaker"),
    (169, "Payroll Heist"),
    (170, "Double-Cross"),
    (171, "Stockpile"),
]

AGGRESSION = [
    (172, "Storm Raider"),
    (173, "BT-1, Blastomech"),
    (174, "0-0-0, Translation and Torture"),
    (175, "Prototype TIE Advanced"),
    (176, "Sebulba's Podracer, Taking the Lead"),
    (177, "Son-tuul Berserkers"),
    (178, "Persecutor, Fire Over Scarif"),
    (179, "Fear and Dead Men"),
    (180, "Inspired Recruit"),
    (181, "Cloud-Rider Veteran"),
    (182, "Weazel, Fighting Back"),
    (183, "B-Wing Skirmisher"),
    (184, "Aerie, Cloud-Rider Dropship"),
    (185, "Ben Solo, Facing the Light"),
    (186, "Enfys Nest's Helmet"),
    (187, '"Staccato Lightning" Repeater'),
    (188, "Savareen Survivor"),
    (189, "Cavern Angels X-Wing"),
    (190, "Haxion Aggressor"),
    (191, "Arvel Skeen, Win and Walk Away"),
    (192, "Bracca Shipbreaker"),
    (193, "Mid Rim Sharpshooter"),
    (194, "Doctor Aphra, Digging For Answers"),
    (195, "Overcharged Transport"),
    (196, "Relentless Hunters"),
    (197, "Shifty Suspects"),
    (198, "Dogged Pursuers"),
    (199, "Ohnaka Gang Bandits"),
    (200, "Salvaged Blaster"),
    (201, "Thermal Detonator"),
    (202, "Commence the Festivities"),
    (203, "Daring Delve"),
    (204, "Every Day, More Lies"),
    (205, "Flash the Vents"),
    (206, "That's a Rock"),
    (207, "Attack From All Sides"),
    (208, "Collateral Damage"),
]

CUNNING = [
    (209, "Nihil Stormsower"),
    (210, "Salacious Crumb, Cackling Companion"),
    (211, "Black Sun Patroller"),
    (212, "Malakili, Keeper of the Menagerie"),
    (213, "Cutthroat Podracer"),
    (214, "Boba Fett, For a Price"),
    (215, "Vermillion, Qi'ra's Auction House"),
    (216, "Jabba's Rancor, Snack Time!"),
    (217, "Hold For Questioning"),
    (218, "Artful Pickpocket"),
    (219, "Anakin's Podracer, So Wizard!"),
    (220, "Wookiee Guerilla"),
    (221, "Lieutenant Gorn, I Deserve Worse"),
    (222, "Rebel Blockade Runner"),
    (223, "Rose Tico, Now It's Worth It"),
    (224, "Liberty, Draw Their Fire!"),
    (225, "Han's Golden Dice"),
    (226, "Secret Battle of Pretend"),
    (227, "Rookie Rocket-Jumper"),
    (228, "Canyon Frontrunner"),
    (229, "The Master Codebreaker, High Stakes"),
    (230, "Ohnaka Gang Starhopper"),
    (231, "Weequay Pirate"),
    (232, "Champion's KT9 Podracer"),
    (233, "Galen Erso, Destroying His Creation"),
    (234, "Kage Elite"),
    (235, "Lady Proxima, Where's the Money?"),
    (236, "Bix Caleen, Selling Scrap"),
    (237, "Qui-Gon Jinn, Influencing Chance"),
    (238, "Scavenging Sandcrawler"),
    (239, "Guild Ambush Team"),
    (240, "Milodon Rider"),
    (241, "The Blade Wing, The Secret of Shantipole"),
    (242, "Improvise"),
    (243, "Transmission Jamming"),
    (244, "Unmarked Credits"),
    (245, "Salvaged Materials"),
    (246, "The Axe Forgets"),
    (247, "Backed by the Hutts"),
    (248, "Windfall"),
]

VILLAINY = [
    (249, "Black Sun Cabalist"),
    (250, "Callous Bounty Hunter"),
    (251, "Night Wind Assailants"),
    (252, "Fett's Firespray, In Pursuit"),
]

HEROISM = [
    (253, "Alliance X-Wing"),
    (254, "Stalwart Fleet Trooper"),
    (255, "Circuit Challenger"),
    (256, "Fire Across The Galaxy"),
]

GRAY = [
    (257, "Hidden Hand Supplier"),
    (258, "Criminal Contact"),
    (259, "Cartel Heavy Fighter"),
    (260, "Seasoned Tracker"),
    (261, "Street Gang Recruiter"),
    (262, "Bank Job Fugitives"),
    (263, "Kessel Hulk"),
    (264, "From a Certain Point of View"),
]

MULTICOLOR = [
    (31,  "Bossk, Join Our Merry Band"),
    (32,  "Cad Bane, Now It's My Turn"),
    (33,  "Hound's Tooth, Hunters' Approach"),
    (34,  "Chewbacca, Mighty Rescuer"),
    (35,  "Ezra Bridger, Spectre Six"),
    (36,  "Obi-Wan Kenobi, Protector of Felucia"),
    (37,  "Han Solo, Hibernation Sick"),
    (38,  "Lepi Lookout"),
    (39,  "Latts Razzi, Deadly Whipmaster"),
    (40,  "Taramyn Barcona, Eyes Front!"),
    (41,  "Nothing Left to Fear"),
    (42,  "IG-88, Programmed to Kill"),
    (43,  "Shadow Cloaking"),
    (44,  "Single Reactor Ignition"),
    (45,  "Zeb Orellios, Spectre Four"),
    (46,  "Chirrut Imwe, I Don't Need Luck"),
    (47,  "Baze Malbus, Good Luck"),
    (48,  "Chio Fain, Four-Armed Slicer"),
    (49,  "Bith Brute"),
    (50,  "Honnah, OINK! SQUEE!"),
    (51,  "Beilert Valance, Target: Vader"),
    (52,  "The Mandalorian, Let's See the Puck"),
    (53,  "Dengar, Take Your Shot"),
    (54,  "Maul, Master of the Shadow Collective"),
    (55,  "Chopper, Spectre Three"),
    (56,  "Cassian Andor, Everything For the Rebellion"),
    (57,  "Benthic Two Tubes, The War Has Just Begun"),
    (58,  "Honor-Bound Partisan"),
    (59,  "Highsinger, Deadly Droid"),
    (60,  "Quarren Contractor"),
    (61,  "Asajj Ventress, Reluctant Hunter"),
    (62,  "Defiant Hammerhead"),
    (63,  "L3-37, Radical Instigator"),
    (64,  "Zuckuss, Dangerous"),
    (65,  "4-LOM, Devious"),
    (66,  "Tear This Ship Apart"),
    (67,  "Jyn Erso, Take the Next Chance"),
    (68,  "Millennium Falcon, Dodging Patrols"),
    (69,  "The Ghost, Home of the Spectres"),
    (70,  "Devaronian Doorbuster"),
    (71,  "The Max Rebo Band, Jatz-Wailers"),
    (72,  "Max Rebo, Encore!"),
    (73,  "Patient Hunter"),
    (74,  "Maz Kanata, Where's My Boyfriend?"),
    (75,  "Interrogation Droid"),
    (76,  "Vult Skerris's Defender, Secret Project"),
    (77,  "Shadow of Stygeon Prime"),
    (78,  "Sabine Wren, Spectre Five"),
    (79,  "K-2SO, Locking the Vault"),
    (80,  "Luke Skywalker, Profit or Be Destroyed"),
    (81,  "Sullustan Sapper"),
    (82,  "Urrr'k, Elite Sharpshooter"),
    (83,  "Broken Horn, Vizago's Pride"),
    (84,  "Krrsantan, Hit and Run"),
    (85,  "You Hold This"),
    (86,  "The Stranger, No Survivors"),
    (87,  "Jango Fett, Wily Mercenary"),
    (88,  "Anakin Skywalker, Prescient Podracer"),
    (89,  "Kanan Jarrus, Spectre One"),
    (90,  "Toydarian Technician"),
    (91,  "Val, It's Been a Ride, Babe"),
    (92,  "Two-Faced Troig"),
    (93,  "Rio Durant, Beckett's Right Hands"),
    (94,  "Hondo Ohnaka, Plays By His Own Rules"),
    (95,  "Finn, Looking Closer"),
    (96,  "Rhydonium Detonation"),
]

SECTION_CARDS = {
    "vigilance": VIGILANCE,
    "command":   COMMAND,
    "aggression": AGGRESSION,
    "cunning":   CUNNING,
    "villainy":  VILLAINY,
    "heroism":   HEROISM,
    "gray":      GRAY,
    "multicolor": MULTICOLOR,
}

# ── Extraction prompts (two per pool: front page, back page) ─────────────────

SYSTEM_PROMPT = """You are a data extractor reading Star Wars: Unlimited sealed deck registration forms.

Each card row has: [PLAYED box] [TOTAL box] [large printed card number] [card name]
- PLAYED box = small handwritten box (LEFTMOST): copies in player's DECK
- TOTAL box = small handwritten box (SECOND from left): copies in player's SEALED POOL
- The large printed number after the boxes is the CARD COLLECTOR NUMBER — ignore it for TOTAL/PLAYED values
- For leaders/bases: a checkmark or X = 1, blank = 0

CRITICAL — COLUMN SEPARATION:
The two small boxes before each card form a pair: [PLAYED][TOTAL]
- Players write a number (1, 2, 3) in the TOTAL box for cards they opened in their pool
- Players write a number in the PLAYED box ONLY for cards they put in their deck (same number as total, or 1 if total≥2 and they only play 1 copy)
- A blank PLAYED box means played=0, even if the TOTAL box has a number

Examples:
  [blank][1]  →  total=1, played=0  (in pool, not in deck — the COMMON case)
  [1][1]      →  total=1, played=1  (in pool AND in deck)
  [blank][2]  →  total=2, played=0  (2 copies in pool, neither played)
  [1][2]      →  total=2, played=1  (2 copies in pool, 1 played)
  [blank][blank] → total=0, played=0

A number in the TOTAL (right) box does NOT mean PLAYED (left) is also filled.
The left box must have its OWN written digit for PLAYED > 0.
MOST cards will be [blank][1] (total=1, played=0).

DECK STRUCTURE — use this to self-check your work:
- Exactly 1 leader played, exactly 1 base played
- Exactly 30 regular card copies played across all non-leader/base sections
- Total played entries across the entire sheet = 32 (1 leader + 1 base + 30 cards)
- Players choose TWO aspect colors: two sections will have many played cards, the other two will have few or zero

If your played totals deviate significantly from these numbers, you have confused the PLAYED and TOTAL columns in one or more sections. Re-examine only the leftmost box of each row in that section.

CRITICAL — NUMBER RANGE:
TOTAL and PLAYED values are ALWAYS 0, 1, 2, or at most 3.
If you read a larger number it is the card collector number — set that row's values to 0.

Be exhaustive: read EVERY row in every section top to bottom. Return ONLY compact JSON."""

FRONT_POOL_PROMPT = """This is PAGE 1 (front) of a sealed deck form.

Your task: extract pool (TOTAL) counts only. Ignore the PLAYED (leftmost) box entirely — set p=0 for all cards.

REMINDER: TOTAL values are always 0, 1, 2, or at most 3. The large printed numbers are card collector numbers — ignore them.

Extract:
1. Player info (top of page): first name, last name, SWU ID, verifier names/IDs, table number
2. LEADER section: which of rows 1-18 have a mark in their TOTAL box (in pool)
3. BASE section: which base is marked
4. VIGILANCE (BLUE) section (middle column): all cards with TOTAL > 0, all with p=0
5. COMMAND (GREEN) section (right column): all cards with TOTAL > 0, all with p=0

Leader rows: 1=Saw Gerrera  2=Tobias Beckett  3=Agent Kallus  4=Aurra Sing  5=Jyn Erso
6=Vel Sartha  7=Boba Fett  8=Director Krennic  9=Hera Syndulla  10=Leia Organa
11=Darth Vader  12=Sebulba  13=Chewbacca  14=Enfys Nest  15=Jabba the Hutt
16=The Client  17=Han Solo  18=Lando Calrissian

Return ONLY this JSON (omit cards with t=0):
{
  "player_first_name": "...", "player_last_name": "...", "player_swu_id": "...",
  "verifier_first_name": "...", "verifier_last_name": "...", "verifier_swu_id": "...",
  "table_num": null,
  "leaders_in_pool": [4, 11, 14],
  "base": "Partisan Hideout (Yellow)",
  "vigilance": [{"n": 102, "t": 1, "p": 0}, {"n": 108, "t": 2, "p": 0}],
  "command": [{"n": 139, "t": 1, "p": 0}]
}"""

def make_front_played_prompt(leaders_in_pool: list, vig_nums: list, cmd_nums: list) -> str:
    return f"""This is PAGE 1 (front) of a sealed deck form. The TOTAL (pool count) boxes have been whited out — only the PLAYED boxes remain visible.

Your task: for each card number listed below, check whether the LEFTMOST small box has a handwritten digit inside it.

HOW TO IDENTIFY A PLAYED MARK (critical — read carefully):
- PLAYED (marked): the box has a handwritten digit (usually "1") visibly written INSIDE the box center
- NOT PLAYED (empty): the box has ONLY the printed box border lines — the interior is blank/white
- Row separator lines are HORIZONTAL. A box with only horizontal lines at top/bottom is EMPTY.
- When in doubt, mark as NOT played. False negatives are better than false positives.

DECK SIZE CONSTRAINT: A SWU sealed deck has exactly 30 non-leader cards. Across BOTH pages combined, exactly 30 regular card numbers should appear in played arrays. This front page typically accounts for 10–20 of those 30. If your vigilance_played + command_played combined exceeds 20, re-examine — you are very likely overcounting.

ONLY check these specific card numbers:
- LEADER rows in pool: {leaders_in_pool} → report exactly ONE as leader_played
- VIGILANCE cards in pool: {vig_nums}
- COMMAND cards in pool: {cmd_nums}

Return ONLY this JSON (include only card numbers where leftmost box clearly has a handwritten digit):
{{
  "leader_played": 7,
  "vigilance_played": [129],
  "command_played": [145, 152, 168]
}}"""

BACK_POOL_PROMPT = """This is PAGE 2 (back) of a sealed deck form.

Your task: extract pool (TOTAL) counts only. Ignore the PLAYED (leftmost) box entirely — set p=0 for all cards.

REMINDER: TOTAL values are always 0, 1, 2, or at most 3. Large printed numbers are card collector numbers — ignore them.

Sections on this page:
- AGGRESSION (RED): left column, card numbers 172-208
- CUNNING (YELLOW): middle column, card numbers 209-248
- MULTICOLOR: right column, card numbers 31-96
- VILLAINY (BLACK): lower left, card numbers 249-252
- HEROISM (WHITE): lower left below Villainy, card numbers 253-256
- NO ASPECT (GRAY): lower middle, card numbers 257-264

For each card with TOTAL > 0, include: {"n": <card_number>, "t": <TOTAL>, "p": 0}
Read ALL rows in every section top to bottom.

Return ONLY this JSON (omit cards with t=0):
{
  "aggression": [{"n": 172, "t": 1, "p": 0}],
  "cunning":    [{"n": 211, "t": 1, "p": 0}],
  "multicolor": [{"n": 31,  "t": 1, "p": 0}],
  "villainy":   [],
  "heroism":    [],
  "gray":       [{"n": 258, "t": 1, "p": 0}]
}"""

def make_back_played_prompt(agg_nums: list, cun_nums: list, mul_nums: list,
                             vil_nums: list, her_nums: list, gray_nums: list) -> str:
    return f"""This is PAGE 2 (back) of a sealed deck form. The TOTAL (pool count) boxes have been whited out — only the PLAYED boxes remain visible.

Your task: for each card number listed below, check whether the LEFTMOST small box has a handwritten digit inside it.

HOW TO IDENTIFY A PLAYED MARK (critical — read carefully):
- PLAYED (marked): the box has a handwritten digit (usually "1") visibly written INSIDE the box center
- NOT PLAYED (empty): the box has ONLY the printed box border lines — the interior is blank/white
- Row separator lines are HORIZONTAL. A box with only horizontal lines at top/bottom is EMPTY.
- When in doubt, mark as NOT played. False negatives are better than false positives.

DECK SIZE CONSTRAINT: A SWU sealed deck has exactly 30 non-leader cards total across both pages. This back page typically accounts for 10–20 of those 30. If your combined total across all sections exceeds 20, re-examine — you are very likely overcounting.

ONLY check these specific card numbers:
- AGGRESSION cards in pool: {agg_nums}
- CUNNING cards in pool: {cun_nums}
- MULTICOLOR cards in pool: {mul_nums}
- VILLAINY cards in pool: {vil_nums}
- HEROISM cards in pool: {her_nums}
- GRAY cards in pool: {gray_nums}

Return ONLY this JSON (empty array if none played in that section):
{{
  "aggression_played": [184, 191],
  "cunning_played": [211, 228, 235],
  "multicolor_played": [52, 67],
  "villainy_played": [],
  "heroism_played": [],
  "gray_played": []
}}"""


def render_page(doc: fitz.Document, page_idx: int, played_only: bool = False) -> str:
    """Render a PDF page to base64-encoded JPEG.

    played_only: white out the TOTAL (pool) box columns so the model sees only
    the PLAYED (leftmost) box marks, eliminating column confusion.
    """
    page = doc[page_idx]
    mat = fitz.Matrix(DPI_SCALE, DPI_SCALE)
    pix = page.get_pixmap(matrix=mat)
    if played_only:
        is_back = (page_idx % 2 == 1)
        strips = _BACK_TOTAL_STRIPS_PT if is_back else _FRONT_TOTAL_STRIPS_PT
        white = (255, 255, 255)
        for x1_pt, x2_pt in strips:
            x1_px = int(x1_pt * DPI_SCALE)
            x2_px = int(x2_pt * DPI_SCALE)
            pix.set_rect(fitz.IRect(x1_px, 0, x2_px, pix.height), white)
    return base64.standard_b64encode(pix.tobytes("jpeg", jpg_quality=92)).decode()


# ── Pool 1 few-shot reference answers (from human-corrected data) ─────────────
# These are embedded as prior conversation turns so the model sees the exact
# visual→JSON mapping for a real, verified pool before reading any new pool.

POOL1_FRONT_POOL_ANSWER = json.dumps({
    "player_first_name": "Ethan", "player_last_name": "Gao",
    "player_swu_id": "7770392", "verifier_first_name": "Collin",
    "verifier_last_name": "Tullb", "verifier_swu_id": "7735371",
    "table_num": 1,
    "leaders_in_pool": [1, 7, 9, 11, 17],
    "base": "Partisan Hideout (Yellow)",
    "vigilance": [
        {"n": 98,  "t": 2, "p": 0}, {"n": 102, "t": 1, "p": 0},
        {"n": 114, "t": 1, "p": 0}, {"n": 116, "t": 2, "p": 0},
        {"n": 118, "t": 1, "p": 0}, {"n": 120, "t": 1, "p": 0},
        {"n": 124, "t": 1, "p": 0}, {"n": 127, "t": 1, "p": 0},
        {"n": 129, "t": 1, "p": 0},
    ],
    "command": [
        {"n": 137, "t": 1, "p": 0}, {"n": 138, "t": 1, "p": 0},
        {"n": 147, "t": 1, "p": 0}, {"n": 153, "t": 1, "p": 0},
        {"n": 157, "t": 1, "p": 0}, {"n": 158, "t": 1, "p": 0},
        {"n": 161, "t": 2, "p": 0}, {"n": 162, "t": 2, "p": 0},
        {"n": 164, "t": 2, "p": 0}, {"n": 165, "t": 1, "p": 0},
        {"n": 166, "t": 1, "p": 0}, {"n": 171, "t": 1, "p": 0},
    ],
})

POOL1_FRONT_PLAYED_ANSWER = json.dumps({
    "leader_played": 7,
    "vigilance_played": [102, 114, 116, 118, 120, 124, 127],
    "command_played": [137, 153, 157, 161, 166, 171],
})

POOL1_BACK_POOL_ANSWER = json.dumps({
    "aggression": [
        {"n": 172, "t": 1, "p": 0}, {"n": 175, "t": 1, "p": 0},
        {"n": 177, "t": 1, "p": 0}, {"n": 183, "t": 1, "p": 0},
        {"n": 184, "t": 1, "p": 0}, {"n": 186, "t": 1, "p": 0},
        {"n": 187, "t": 1, "p": 0}, {"n": 189, "t": 1, "p": 0},
        {"n": 190, "t": 1, "p": 0}, {"n": 192, "t": 1, "p": 0},
        {"n": 195, "t": 1, "p": 0}, {"n": 197, "t": 1, "p": 0},
        {"n": 198, "t": 1, "p": 0}, {"n": 202, "t": 1, "p": 0},
        {"n": 203, "t": 1, "p": 0}, {"n": 204, "t": 1, "p": 0},
        {"n": 206, "t": 1, "p": 0}, {"n": 207, "t": 1, "p": 0},
    ],
    "cunning": [
        {"n": 216, "t": 2, "p": 0}, {"n": 218, "t": 1, "p": 0},
        {"n": 220, "t": 1, "p": 0}, {"n": 228, "t": 1, "p": 0},
        {"n": 229, "t": 1, "p": 0}, {"n": 230, "t": 1, "p": 0},
        {"n": 231, "t": 2, "p": 0}, {"n": 234, "t": 1, "p": 0},
        {"n": 236, "t": 1, "p": 0}, {"n": 239, "t": 1, "p": 0},
        {"n": 240, "t": 1, "p": 0}, {"n": 241, "t": 1, "p": 0},
        {"n": 242, "t": 2, "p": 0}, {"n": 244, "t": 1, "p": 0},
    ],
    "multicolor": [
        {"n": 31, "t": 1, "p": 0}, {"n": 40, "t": 1, "p": 0},
        {"n": 48, "t": 1, "p": 0}, {"n": 49, "t": 2, "p": 0},
        {"n": 52, "t": 1, "p": 0}, {"n": 53, "t": 1, "p": 0},
        {"n": 59, "t": 1, "p": 0}, {"n": 60, "t": 1, "p": 0},
        {"n": 62, "t": 1, "p": 0}, {"n": 66, "t": 1, "p": 0},
        {"n": 69, "t": 1, "p": 0}, {"n": 74, "t": 1, "p": 0},
        {"n": 75, "t": 1, "p": 0}, {"n": 90, "t": 1, "p": 0},
    ],
    "villainy": [
        {"n": 249, "t": 1, "p": 0}, {"n": 250, "t": 1, "p": 0},
        {"n": 252, "t": 2, "p": 0},
    ],
    "heroism": [],
    "gray": [
        {"n": 257, "t": 1, "p": 0}, {"n": 258, "t": 1, "p": 0},
        {"n": 261, "t": 1, "p": 0}, {"n": 263, "t": 1, "p": 0},
    ],
})

POOL1_BACK_PLAYED_ANSWER = json.dumps({
    "aggression_played": [175, 183, 186, 189, 190, 195, 198, 203, 206],
    "cunning_played": [216, 218, 228, 231, 234, 241],
    "multicolor_played": [31, 40, 52, 59],
    "villainy_played": [252],
    "heroism_played": [],
    "gray_played": [258],
})

_ref_images: dict | None = None

def _get_ref_images(doc: fitz.Document) -> dict:
    """Render pool 1 pages once and cache them for use as few-shot examples."""
    global _ref_images
    if _ref_images is None:
        _ref_images = {
            "front_jpeg":        render_page(doc, 0),
            "back_jpeg":         render_page(doc, 1),
            "front_played_only": render_page(doc, 0, played_only=True),
            "back_played_only":  render_page(doc, 1, played_only=True),
        }
    return _ref_images


def _parse_json_response(text: str) -> dict:
    raw = text.strip()
    if "```" in raw:
        after = raw.split("```", 1)[1]
        if after.startswith("json"):
            after = after[4:]
        raw = after.rsplit("```", 1)[0].strip()
    elif not raw.startswith("{"):
        start = raw.find("{")
        if start >= 0:
            raw = raw[start:]
    if not raw:
        raise ValueError("Empty JSON in response")
    return json.loads(raw)


def extract_pool(doc: fitz.Document, pool_num: int) -> dict:
    """Four API calls per pool: pool counts and played counts extracted separately per page."""
    client = anthropic.Anthropic()

    front_jpeg        = render_page(doc, (pool_num - 1) * 2)
    back_jpeg         = render_page(doc, (pool_num - 1) * 2 + 1)
    front_played_only = render_page(doc, (pool_num - 1) * 2,     played_only=True)
    back_played_only  = render_page(doc, (pool_num - 1) * 2 + 1, played_only=True)

    # Load pool 1 reference images (cached after first call); skip for pool 1 itself
    ref = _get_ref_images(doc) if pool_num != 1 else None

    def _img(b64: str, mime: str = "image/jpeg") -> dict:
        return {"type": "image", "source": {"type": "base64", "media_type": mime, "data": b64}}

    def call(image_b64: str, mime: str, prompt: str, model: str,
             ref_image_b64: str | None, ref_mime: str, ref_answer: str,
             ref_prompt_override: str | None = None) -> dict:
        messages = []
        if ref_image_b64 is not None:
            ref_prompt = ref_prompt_override if ref_prompt_override is not None else prompt
            messages += [
                {"role": "user",      "content": [_img(ref_image_b64, ref_mime), {"type": "text", "text": ref_prompt}]},
                {"role": "assistant", "content": ref_answer},
            ]
        messages.append({"role": "user", "content": [_img(image_b64, mime), {"type": "text", "text": prompt}]})

        for attempt in range(3):
            r = client.messages.create(
                model=model,
                max_tokens=4096,
                system=SYSTEM_PROMPT,
                messages=messages,
            )
            try:
                return _parse_json_response(r.content[0].text)
            except (ValueError, json.JSONDecodeError) as e:
                if attempt == 2:
                    raise
                print(f"  [retry {attempt+1}] JSON parse failed: {e}")

    ref_front_jpeg        = ref["front_jpeg"]        if ref else None
    ref_back_jpeg         = ref["back_jpeg"]         if ref else None
    ref_front_played_only = ref["front_played_only"] if ref else None
    ref_back_played_only  = ref["back_played_only"]  if ref else None

    # Pool calls first so we can pass card numbers to the played prompts
    front_pool = call(front_jpeg, "image/jpeg", FRONT_POOL_PROMPT, MODEL_POOL,
                      ref_front_jpeg, "image/jpeg", POOL1_FRONT_POOL_ANSWER)
    back_pool  = call(back_jpeg,  "image/jpeg", BACK_POOL_PROMPT,  MODEL_POOL,
                      ref_back_jpeg,  "image/jpeg", POOL1_BACK_POOL_ANSWER)

    def _nums(data: dict, sec: str) -> list:
        return [e["n"] for e in data.get(sec, [])]

    # Build reference played prompts using pool 1's known card numbers
    p1f = json.loads(POOL1_FRONT_POOL_ANSWER)
    p1b = json.loads(POOL1_BACK_POOL_ANSWER)
    ref_front_played_prompt = make_front_played_prompt(
        p1f["leaders_in_pool"], _nums(p1f, "vigilance"), _nums(p1f, "command"))
    ref_back_played_prompt = make_back_played_prompt(
        _nums(p1b, "aggression"), _nums(p1b, "cunning"), _nums(p1b, "multicolor"),
        _nums(p1b, "villainy"), _nums(p1b, "heroism"), _nums(p1b, "gray"))

    # Build this pool's played prompts using its own pool data
    front_played_prompt = make_front_played_prompt(
        front_pool.get("leaders_in_pool", []),
        _nums(front_pool, "vigilance"), _nums(front_pool, "command"))
    back_played_prompt = make_back_played_prompt(
        _nums(back_pool, "aggression"), _nums(back_pool, "cunning"), _nums(back_pool, "multicolor"),
        _nums(back_pool, "villainy"), _nums(back_pool, "heroism"), _nums(back_pool, "gray"))

    # Played calls use the pool-column-whited-out image so the model can only see PLAYED marks
    front_played = call(front_played_only, "image/jpeg", front_played_prompt, MODEL_PLAYED,
                        ref_front_played_only, "image/jpeg", POOL1_FRONT_PLAYED_ANSWER,
                        ref_prompt_override=ref_front_played_prompt)
    back_played  = call(back_played_only,  "image/jpeg", back_played_prompt,  MODEL_PLAYED,
                        ref_back_played_only,  "image/jpeg", POOL1_BACK_PLAYED_ANSWER,
                        ref_prompt_override=ref_back_played_prompt)

    # Build played sets (card numbers) per section from the focused played calls
    def played_set(key: str, data: dict) -> set:
        return set(data.get(key) or [])

    vig_played  = played_set("vigilance_played",  front_played)
    cmd_played  = played_set("command_played",    front_played)
    agg_played  = played_set("aggression_played", back_played)
    cun_played  = played_set("cunning_played",    back_played)
    mul_played  = played_set("multicolor_played", back_played)
    vil_played  = played_set("villainy_played",   back_played)
    her_played  = played_set("heroism_played",    back_played)
    gray_played = played_set("gray_played",       back_played)

    played_by_sec = {
        "vigilance":  vig_played,
        "command":    cmd_played,
        "aggression": agg_played,
        "cunning":    cun_played,
        "multicolor": mul_played,
        "villainy":   vil_played,
        "heroism":    her_played,
        "gray":       gray_played,
    }

    # Merge pool and played data
    merged = {**front_pool}
    merged["leader_played"] = front_played.get("leader_played")
    for sec in ("aggression", "cunning", "multicolor", "villainy", "heroism", "gray"):
        merged[sec] = back_pool.get(sec, [])

    # Flatten all card sections, applying played flags from the separate played call
    cards = []
    for sec in ("vigilance", "command", "aggression", "cunning", "villainy", "heroism", "gray", "multicolor"):
        p_set = played_by_sec.get(sec, set())
        for entry in merged.get(sec, []):
            t = entry.get("t") or 0
            n = entry.get("n")
            if t > 3:
                t = 0
            p = min(1, t) if (n in p_set) else 0
            if t > 0 or p > 0:
                cards.append({"s": sec, "n": n, "t": t, "p": p})
    merged["cards"] = cards

    return merged


def validate_pool(data: dict) -> list[str]:
    """Return list of validation warnings (non-blocking)."""
    warnings = []

    n_leaders = len(data.get("leaders_in_pool") or [])
    if n_leaders != 6:
        warnings.append(f"Expected 6 leaders in pool, got {n_leaders}")

    if not data.get("leader_played"):
        warnings.append("No played leader detected")

    total_cards = sum((c.get("t") or 0) for c in data.get("cards") or [])
    if total_cards != 96:
        warnings.append(f"Expected 96 cards in pool, got {total_cards}")

    played_cards = sum((c.get("p") or 0) for c in data.get("cards") or [])
    if played_cards < 28:
        warnings.append(f"Expected ≥30 cards played, got {played_cards}")
    elif played_cards > 38:
        warnings.append(f"Expected ≤35 cards played, got {played_cards} (likely overcounting)")

    if not data.get("base"):
        warnings.append("No base found")

    return warnings


def save_pool(data: dict, pool_num: int, dry_run: bool) -> int | None:
    """Insert pool into DB. Returns pool_id or None if dry_run."""
    warnings = validate_pool(data)
    ocr_notes = "; ".join(warnings) if warnings else None

    # Resolve leader names from row numbers
    leaders_in_pool = data.get("leaders_in_pool") or []
    leader_played_row = data.get("leader_played")
    leader_names_in_pool = [LEADERS[r - 1][1] for r in leaders_in_pool if 1 <= r <= 18]
    leader_played_name = LEADERS[leader_played_row - 1][1] if leader_played_row and 1 <= leader_played_row <= 18 else None

    cards = data.get("cards") or []
    total_cards = sum((c.get("t") or 0) for c in cards)
    played_cards = sum((c.get("p") or 0) for c in cards)

    if dry_run:
        print(f"\n[Pool {pool_num}] DRY RUN")
        print(f"  Player: {data.get('player_first_name')} {data.get('player_last_name')} (SWU: {data.get('player_swu_id')})")
        print(f"  Table: {data.get('table_num')}")
        print(f"  Base: {data.get('base')}")
        print(f"  Leaders in pool ({len(leader_names_in_pool)}): {leader_names_in_pool}")
        print(f"  Leader played: {leader_played_name}")
        print(f"  Cards in pool: {total_cards}  Cards played: {played_cards}")
        if warnings:
            print(f"  WARNINGS: {warnings}")
        return None

    pool_id = db.fetchone(
        """INSERT INTO sealed_pools
           (scan_page, table_num,
            player_first_name, player_last_name, player_swu_id,
            verifier_first_name, verifier_last_name, verifier_swu_id,
            front_scanned, back_scanned, ocr_notes)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,true,true,%s)
           RETURNING id""",
        (
            pool_num,
            data.get("table_num"),
            data.get("player_first_name"),
            data.get("player_last_name"),
            data.get("player_swu_id"),
            data.get("verifier_first_name"),
            data.get("verifier_last_name"),
            data.get("verifier_swu_id"),
            ocr_notes,
        ),
    )["id"]

    # Card lookup by number
    num_to_name = {num: name for sec in SECTION_CARDS.values() for num, name in sec}

    # Insert leaders (all in pool get pool_count=1; played leader gets played_count=1)
    for row_num in leaders_in_pool:
        if not (1 <= row_num <= 18):
            continue
        _, name = LEADERS[row_num - 1]
        played = 1 if row_num == leader_played_row else 0
        db.execute(
            "INSERT INTO sealed_pool_cards (pool_id,section,card_number,card_name,pool_count,played_count) VALUES (%s,'leader',%s,%s,1,%s)",
            (pool_id, row_num, name, played),
        )

    # Insert base
    base_name = data.get("base")
    if base_name:
        db.execute(
            "INSERT INTO sealed_pool_cards (pool_id,section,card_number,card_name,pool_count,played_count) VALUES (%s,'base',NULL,%s,1,1)",
            (pool_id, base_name),
        )

    # Insert regular cards (only non-zero entries)
    valid_sections = {"vigilance", "command", "aggression", "cunning",
                      "villainy", "heroism", "gray", "multicolor"}
    for entry in cards:
        section = entry.get("s", "")
        if section not in valid_sections:
            continue
        num = entry.get("n")
        total = entry.get("t") or 0
        played = entry.get("p") or 0
        if total > 0 or played > 0:
            name = num_to_name.get(num, f"Unknown #{num}")
            db.execute(
                "INSERT INTO sealed_pool_cards (pool_id,section,card_number,card_name,pool_count,played_count) VALUES (%s,%s,%s,%s,%s,%s)",
                (pool_id, section, num, name, total, played),
            )

    print(f"[Pool {pool_num}] saved → pool_id={pool_id}", end="")
    if warnings:
        print(f"  WARN: {warnings}", end="")
    print()
    return pool_id


def already_processed(pool_num: int) -> bool:
    row = db.fetchone("SELECT id FROM sealed_pools WHERE scan_page=%s", (pool_num,))
    return row is not None


def delete_pool(pool_num: int):
    db.execute("DELETE FROM sealed_pools WHERE scan_page=%s", (pool_num,))


def main():
    parser = argparse.ArgumentParser(description="OCR sealed PQ pool decklists into DB")
    parser.add_argument("--pool", help="Pool number or range (e.g. 3 or 1-10)")
    parser.add_argument("--dry-run", action="store_true", help="Print extracted data, no DB writes")
    parser.add_argument("--reprocess", type=int, help="Reprocess pool N even if already in DB")
    args = parser.parse_args()

    doc = fitz.open(str(PDF_PATH))
    total_pools = len(doc) // 2
    print(f"PDF: {len(doc)} pages = {total_pools} pools")

    # Determine which pools to process
    if args.reprocess:
        pools = [args.reprocess]
    elif args.pool:
        if "-" in args.pool:
            a, b = args.pool.split("-")
            pools = list(range(int(a), int(b) + 1))
        else:
            pools = [int(args.pool)]
    else:
        pools = list(range(1, total_pools + 1))

    for pool_num in pools:
        if pool_num < 1 or pool_num > total_pools:
            print(f"[Pool {pool_num}] out of range (1-{total_pools}), skipping")
            continue

        if not args.dry_run and not args.reprocess:
            if already_processed(pool_num):
                print(f"[Pool {pool_num}] already in DB, skipping")
                continue

        if args.reprocess == pool_num:
            print(f"[Pool {pool_num}] deleting existing records for reprocess")
            delete_pool(pool_num)

        print(f"[Pool {pool_num}] extracting...", end=" ", flush=True)
        try:
            data = extract_pool(doc, pool_num)
            print("OK")
        except Exception as e:
            print(f"ERROR: {e}")
            continue

        try:
            save_pool(data, pool_num, args.dry_run)
        except Exception as e:
            print(f"[Pool {pool_num}] DB error: {e}")
            continue

        if not args.dry_run and len(pools) > 1:
            time.sleep(1)  # brief pause between API calls


if __name__ == "__main__":
    main()
