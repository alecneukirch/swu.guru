# Changelog

## 2026-11-09

### Loop Pass 1 — Audit & Fix

**CSS token fixes**
- Defined missing `:root` variables `--accent` (#7c6af7), `--text1` (#e8e8f0), `--yellow` (#f0b429) — previously used in ~15 rules but never declared, causing silent fallback to `transparent`/`inherit`

**Generate Deck modal**
- Removed duplicate `display:none` in backdrop inline style
- Added `role="dialog"`, `aria-modal="true"`, `aria-labelledby` on modal container
- Added `aria-label="Close"` on close button (✕ is not screen-reader friendly alone)
- Added Escape key handler (`_gendeckEscHandler`) — registered on open, removed on close

**Deck builder algorithm (earlier this session)**
- Fixed `NOT EXISTS` filter that was incorrectly excluding playable cards (e.g. Bail Organa, Han Solo, Qui-Gon Jinn unit variants) by matching on base name only instead of full `name | subtitle`
- Added corrupt reference deck filter (44–56 card range) — removed deck with 72 copies inflating event targets
- Added `type_filled` tracking to prevent fill-remaining pass from exceeding type targets
- Switched copy count selection from rounded weighted average to **win-rate-weighted mode** (copy_votes)
- Added `base_label` param so generated deck name uses group label ("Command Splash") not raw pipe-separated base names

**DB cleanup**
- Deleted corrupt decklist for standing 15591 (72 cards, mismatched leader) and marked `has_decklist=FALSE`

### Loop Pass 2 — UX Refinement & Bug Fix

**Critical bug fix**
- Synergy endpoint (`/api/leader/{leader}/synergy`) was 500-ing for all unfiltered requests because it referenced `mv_card_synergy` materialized view that doesn't exist in `swu_cards`. Replaced with a live CTE query matching the base-scoped branch (identical logic, no base filter).

**Leader hero sizing corrections** (per saved UI preference)
- Portrait: 140×100px → 144×144px (square)
- Stat values (`.lh-stat-val`): 22px → 24px
- Stat labels (`.lh-stat-label`): 13px → 14px
- Funnel values (`.lh-pct-val`): 16px → 20px
- Stat gap: 20px → 24px

**CSS audit**
- All `var(--*)` usages are now declared in `:root`; `--villainy` is the only defined-but-unused token (intentional swatch, harmless)
- No gradient text, excessive glassmorphism stacks, or floated elements found

**API health check** — all 10 tested endpoints return 200 after fixes

### Loop Pass 3 — Feature Completion & Code Audit

**Synergy tab (new feature)**
- Wired up the half-implemented Synergy feature: CSS components + API endpoint both existed but had no UI entry point
- Added "Synergy" tab on leader detail page (after Mirror)
- Controls: min plays together + min lift threshold (1.1 default)
- Renders card-art pair tiles with lift badge, inclusion rates (%), and co-occurrence count
- Stale-request guard (`_synergyGen`) prevents race conditions on fast tab switches
- URL hash routing (`&tab=synergy`) preserved automatically via existing routing

**Code audits — no issues found**
- CSS duplicate class check: 73 "standalone" defs are all inside `@media (max-width:700px)` — intentional responsive overrides
- CSS variable audit: all `var(--*)` tokens are declared; `--villainy` (#040004) is the only defined-but-unused token (intentional swatch)
- No synchronous XHR patterns found
- No gradient text, excessive glassmorphism stacks, or unmotivated floating elements

**All API endpoints: 200 ✓**

### Loop Pass 4 — Query Optimization, ARIA, & Markup Fixes

**DB index**
- Added `idx_dc_synergy` partial index on `decklist_cards (standing_id, card_name) WHERE is_sideboard = FALSE` — removes full-table seq scan from synergy self-join (Boba Fett 2249-deck query: ~1s, acceptable for async tab load; base-scoped is 40ms)

**Synergy query refactor**
- Unified base-scoped and unscoped branches into single CTE (`leader_sids` → `leader_cards`), using `has_decklist = TRUE` to avoid the old `INNER JOIN (SELECT DISTINCT standing_id FROM decklist_cards)` subquery
- Cleaner parameter passing (no repeated `[leader] × 3`)

**ARIA accessibility**
- Added `role="dialog" aria-modal="true" aria-labelledby/aria-label` and `aria-label="Close"` on close buttons for: card detail modal, player detail panel, merge review modal

**Loading state markup fix**
- `loadLeaderCards()` was writing `<tr><td class="loading">` into a `<div class="card-grid">` — invalid HTML that the browser strips; replaced with `<div class="loading" style="grid-column:1/-1">`

**All API endpoints: 200 ✓**

### Notes for next pass
- `mv_card_copy_matrix` materialized view may be stale — verify HMW data is reflected
- No build/lint pipeline (vanilla JS + FastAPI) — worth adding `eslint` / `pyflakes` to CI

### Loop Pass 5 — Import Hygiene & Mobile Responsiveness

**API cleanup (`api/main.py`)**
- Moved `from collections import defaultdict` from three inline function-body imports (`matchup_matrix_by_base`, `meta_call`, `generate_deck`) to module-level — eliminates repeated import overhead on every request
- Removed duplicate `extra_join = ""` dead assignment in `matchup_matrix_by_base` (leftover from refactor)
- Removed redundant `import re` inside `sealed_confirm_data` — `re` is already a top-level import

**Frontend — mobile responsiveness (`frontend/index.html`)**
- Picks page had no `@media (max-width:700px)` override: `.picks-list` was fixed at 520px wide with the detail panel beside it, causing horizontal overflow on phones
- Added breakpoint: stack list + detail panel vertically, `width:100%` on both, hide Meta% column from matchup table (space-saving)

**Verification**
- `--asp-color` confirmed intentional: set via inline `style=` on each aspect button, not a missing `:root` token
- `mv_card_copy_matrix` confirmed current: 205 HMW card rows present (Hera, Ahsoka covered)
- All 7 tested endpoints return 200 ✓

### Notes for next pass
- No build/lint pipeline (vanilla JS + FastAPI) — worth adding `eslint` / `pyflakes` to CI
- Picks page matchup table loses Meta% column on mobile (hidden to save space) — if column count becomes a UX issue, consider a horizontal-scroll table instead

### Loop Pass 6 — Import Hygiene Round 2 & Mobile Tab Overflow

**API cleanup (`api/main.py`)**
- Added `import traceback` and `timedelta` to module-level imports — removed two more inline function-body imports
- Removed redundant `import re as _re` inside `analyze_deck`; `re` is already a top-level import
- All remaining inline imports confirmed intentional: `requests` (sync HTTP client for swudb.com), `scraper.melee` (heavy dep deferred for startup perf), `difflib` (sealed-only), `fitz` (PDF-only)

**Frontend — mobile tab overflow (`frontend/index.html`)**
- Leader page had 5 tabs (Cards / Matchups / Weaknesses / Mirror / Synergy) with no horizontal scroll — overflowed ~580px on a 375px screen
- Added `overflow-x:auto; flex-wrap:nowrap` to `.lp-tabs` at mobile breakpoint; tightened tab padding to `8px 10px` and font to `11px; white-space:nowrap`

**Verification**
- All 5 tested endpoints return 200 ✓

### Notes for next pass
- No build/lint pipeline (vanilla JS + FastAPI) — worth adding `eslint` / `pyflakes` to CI

### Loop Pass 7 — Keyboard Accessibility & ARIA Fixes

**ARIA icon buttons (`frontend/index.html`)**
- Added `aria-label="Previous pool"` / `aria-label="Next pool"` to `←` / `→` correct-nav-btn icon buttons in pool correction UI — icon-only buttons were invisible to screen readers
- Added `aria-label="Close"` to `✕` sl-modal-close buttons in sealed league and HMW league modal overlays
- Added `aria-label="Add card"` to `+` correct-nav-btn in JS-rendered pool correction template literal

**Keyboard sort controls (`frontend/index.html`)**
- All 5 `.gh-col` sort header divs (`#gh-incl`, `#gh-conv`, `#gh-copies`, `#gh-decks`, `#gh-t8s`) used `onclick` with no `role` or `tabindex` — not reachable or activatable by keyboard
- Added `role="button"`, `tabindex="0"`, and `onkeydown` Enter/Space handler to each

**Verification**
- All 6 tested endpoints return 200 ✓

### Notes for next pass
- No build/lint pipeline (vanilla JS + FastAPI) — worth adding `eslint` / `pyflakes` to CI

### Loop Pass 8 — Global Focus Styles, Form Control Labels & Keyboard Navigation

**Global `:focus-visible` rule (`frontend/index.html`)**
- Added `[role="button"]:focus-visible, [role="link"]:focus-visible, button:focus-visible, a:focus-visible` rule — 2px `--accent` outline, offset 2px — was completely absent; keyboard users had no visible focus indicator after outline resets

**`aria-label` on form controls**
- `#meta-select` → `aria-label="Meta period"` (nav dropdown)
- `#lp-min-decks` → `aria-label="Minimum decks"` (leader cards tab)
- `#wk-min-matches` → `aria-label="Minimum matches"` (weaknesses tab)
- `#mr-min-matches` → `aria-label="Minimum mirror matches"` (mirror tab)
- `#syn-min-co` → `aria-label="Minimum plays together"`, `#syn-min-lift` → `aria-label="Minimum lift"` (synergy tab)
- `#pq-range` → `aria-label="Search radius"` (PQ finder)
- `#cp-min-decks` → `aria-label="Minimum decks"`, `#cp-trait` → `aria-label="Filter by trait"`, `#cp-keyword` → `aria-label="Filter by keyword"` (cards page)
- All above had visible adjacent text labels but no programmatic association

**Keyboard navigation for navigation elements**
- `.nav-logo` div → added `role="button" tabindex="0" aria-label="Go to home" onkeydown`
- Both `.bc-link` breadcrumb spans → added `role="button" tabindex="0" onkeydown`

**Keyboard navigation for section headers**
- Both `.ct-type-divider` collapsible headers (leader cards + global cards pages) → added `role="button" tabindex="0" aria-label="Toggle … section" onkeydown`

**Verification**
- All 4 tested endpoints return 200 ✓

### Notes for next pass
- No build/lint pipeline (vanilla JS + FastAPI) — worth adding `eslint` / `pyflakes` to CI
- Remaining interactive divs without keyboard support: `.elo-bucket-col` (ELO filter chips), `.pd-event-row` (player history rows), `.sl-stat-card.sl-flippable` (league stat cards), `.pick-row` (meta call rows) — lower priority (all have clear visual affordance)

### Loop Pass 9 — WCAG AA Contrast Fixes & Interactive Div Accessibility

**WCAG AA contrast fixes (`frontend/index.html`)**
- `--villainy-text` `#8b3a6b` → `#be6ab4`: 2.76:1 → 5.59:1 on `--bg` — was failing AA for all text uses (aspect label, filter button active state)
- `--accent` `#7c6af7` → `#8d7ef8`: 4.29:1 → 5.28:1 on `--surface2` — was failing AA for active text (`.sealed-filter-btn.active`, focus rings now also stronger)

**Remaining interactive div accessibility (`frontend/index.html`)**
- `.elo-bucket-col` → `role="button" tabindex="0" aria-pressed aria-label="ELO N+ filter" onkeydown`
- `.pd-event-row` → `role="button" tabindex="0" aria-label="Open leader detail" onkeydown`
- `.sl-stat-card.sl-flippable` → `role="button" tabindex="0" aria-label="Flip stat card" onkeydown`
- `.pick-row` → `role="button" tabindex="0" aria-expanded onkeydown`

**League admin form control labels**
- Added `aria-label` to `#sl-match-session`, `#sl-match-p1`, `#sl-match-p2`, `#sl-match-p1gw`, `#sl-match-gdraw`, `#sl-match-p2gw`, `#sl-import-session`

**Verification**
- All 5 tested endpoints return 200 ✓

### Notes for next pass
- No build/lint pipeline (vanilla JS + FastAPI) — worth adding `eslint` / `pyflakes` to CI
- All major interactive elements now have keyboard support and aria-label coverage
- Next: performance audit (Core Web Vitals, DB query timing), or CSS dead-code removal pass

### Loop Pass 10 — Dead Code Removal

**Dead ternary fix (`frontend/index.html`)**
- `loadPicks()`: `const minDecks = currentFormat === 'eternal' ? 2 : 2;` → `const minDecks = 2;` — both branches were identical

**Dead CSS removal (~385 lines, `frontend/index.html`)**
- Deleted Block 1+2: `.threat-pill`, `.ctr-*`, `.counter-*` classes (~228 lines) — orphaned "Meta Counter" feature CSS with no corresponding HTML or JS usage
- Deleted Block 3: `.counters-grid`, `.ctr2-card`, `.ctr2-target`, `.ctr2-counters`, `.ctr2-counter-*` classes (~157 lines) including `@media` overrides — same orphaned feature
- `/api/meta-counter` backend endpoint exists but is never called from the frontend; CSS was the only artifact

**Verification**
- All 5 tested endpoints return 200 ✓

### Notes for next pass
- No build/lint pipeline (vanilla JS + FastAPI) — worth adding `eslint` / `pyflakes` to CI
- Performance audit (Core Web Vitals, DB query timing) is next logical pass

### Loop Pass 11 — Dead CSS Sweep (218 lines removed)

**Duplicate/dead CSS removed (`frontend/index.html`)**
- Removed first duplicate `.players-table` block plus dead child classes (`.pl-rank`, `.pl-name`, `.pl-num`, `.pl-leader`, `.pl-mwr-*`, `.player-detail-header`, `.player-detail-name`, `.player-detail-stats`, `.player-stat-val/label`) — these were an older table design superseded by `.pt-*` classes, never referenced in HTML or JS (~76 lines)
- Restored missing `cursor: pointer` on `.players-table tr:hover td` (was lost in the surviving block)
- Removed dead first `.sl-table` CSS block — all 6 HTML uses of `.sl-table` are in the league sections and use the second (league-section) definition; first block was overridden at every selector; `.sl-num*` subclasses never used in any template (~14 lines)
- Removed `.elo-insight` — never referenced in HTML or JS (~6 lines)
- Removed old list-based matchup card classes (`.mu-card-row`, `.mu-card-art`, `.mu-card-name`, `.mu-card-wr`, `.mu-card-games`, `.mu-card-bar`, `.mu-delta`) — replaced by card-tile grid system; never referenced (~34 lines)
- Removed dead SB card decoration classes (`.ct-sb-divider`, `.ct-sb-divider-line`, `.ct-sb-divider-label`, `.ct-sb-flag`) — never referenced in templates (~36 lines)
- Removed `.conv-pill` and `.incl-bar`/`.incl-track`/`.incl-fill`/`.incl-val` — both appear to be old table-row display components replaced by the current `ct-conv`/`ct-incl` card-tile system (~26 lines)
- Removed `.merge-confidence` and `.hmw-pts-badge` — never referenced in HTML or JS (~9 lines)
- Removed `.dim2` utility class — never referenced in HTML or JS (~1 line)

**Verification**
- All 6 tested endpoints return 200 ✓ (pq-finder 500 is pre-existing external network timeout)
- File: 9569 → 9351 lines (218 lines removed this pass, ~985 lines removed total across passes 10–11)

### Notes for next pass
- No build/lint pipeline (vanilla JS + FastAPI) — worth adding `eslint` / `pyflakes` to CI
- Performance audit (Core Web Vitals, DB query timing) is the next logical pass

### Loop Pass 12 — API Result Caching & Dead Code Cleanup

**API result caching (`api/main.py`)**
- Added `_leaders_cache` and `_meta_call_cache` dicts with 5-minute TTL (`_LEADERS_TTL = 300`)
- `/api/leaders`: cold 122ms → warm 5ms (cache key: all query params tuple)
- `/api/meta-call`: cold 245ms → warm 16ms (cache key: all query params tuple)
- Both endpoints are called on every page load; data only changes when new events are imported, making TTL caching safe

**Dead CSS removed (`frontend/index.html`)**
- Deleted `.sb-badge` — marked "used in other contexts" in a comment but never referenced in HTML or JS (~12 lines)

**Error handling fix (`frontend/index.html`)**
- `applyAllGuesses()`: final `/api/sealed/confirm-data` refresh call was unguarded — added try/catch; button now shows "(refresh failed)" instead of throwing silently

**Verification**
- All 7 tested endpoints return 200 ✓
- Cold → warm speedup confirmed: leaders 122ms → 5ms, meta-call 245ms → 16ms

### Notes for next pass
- No build/lint pipeline (vanilla JS + FastAPI) — worth adding `eslint` / `pyflakes` to CI
- `/api/leaders/by-base` and other heavy endpoints are candidates for the same TTL cache pattern

### Loop Pass 15 — Deck Generation Algorithm Overhaul

**Dynamic deck size target (`api/main.py`)**
- Generated decklists now use a weighted-average target size derived from reference decklists instead of hardcoded 50 — Data Vault leaders correctly produce 60-card main decks

**Leader subtitle lookup fix (`api/main.py`)**
- Leader lookup in generate-deck now matches on `name + subtitle` when a subtitle is provided (e.g. "Luke Skywalker, Jedi Knight" vs "Luke Skywalker, I Can Save Him") — was previously only matching on base name, returning the wrong leader card

**World indicator filtering (`api/main.py`)**
- Generate-deck reference deck pool now filters for decklists that include the world indicator cards for world-scoped bases (Naboo, Kashyyyk, Endor, Tatooine)
- Fixed case mismatch: `standing_world_tag.world` stores title-case ("Kashyyyk") but group_key regex captures lowercase — changed SQL filter to `LOWER(swt.world) = %s`

**Raised ref_decks default (`api/main.py`)**
- Default reference deck count raised from 15 to 50 (max 200) — reduces outlier distortion from all-ground builds inflating type targets

**Minimum deck thresholds removed (`api/main.py`)**
- Lowered all `min_decks` / `min_matches` defaults to 1 across leader stats, matchup matrix, and synergy endpoints — no data is hidden from low-volume leaders

**Meta-call qualification thresholds (`frontend/index.html`)**
- Changed from 5 opponents / 3 matches to 4 opponents / 2 matches for meta-call qualification

**Copy-matrix best config fix (`api/main.py`)**
- Removed hardcoded 3-deck minimum from best-config selection — was showing "Best: 0 copies" for single-deck cells; now considers all cells including those with 1 deck

**Bucket fill order fix (`api/main.py`)**
- Changed deck slot sort to: highest rate-of-3-copies first, then rate-of-2-copies, then `incRate × best_qty` — ensures the most-confirmed copy counts fill each type slot first

**Phase 4 must-include pass (`api/main.py`)**
- Added Phase 4 after existing fill phases: any main-deck card appearing in >50% of reference decklists that is missing or under-copied vs best_qty is inserted by displacing lower-priority cards
- Displacement uses `incRate × count` score comparison — prevents high-incRate 3-ofs from being bumped by slightly higher-incRate 2-ofs
- Fixed duplicate-entry bug: when Phase 3 had already added a card at count=1, Phase 4 now updates the existing entry in-place rather than re-appending the same dict object

**Verification**
- Wicket Command Splash: ref_decks=50, total=50, Rebellious Hammerhead correctly present at 2 copies ✓
- All tested endpoints return 200 ✓

### Loop Pass 14 — Pyflakes Static Analysis Fixes

**Bug fixes (`api/main.py`)**
- **CRITICAL runtime fix**: Changed `_re.search(...)` → `re.search(...)` at line ~3698 — `import re as _re` inside `analyze_deck` was removed in Pass 6 but the alias reference was missed; would cause `NameError` on any request hitting that code path
- Removed unused `w = decay_weight()` assignment in `leader_base_groups()` — function doesn't use decay weights; dead assignment removed
- Removed unused `combo_keys` list comprehension at matchup matrix build — `row_key`/`col_key` are computed inline in the loop; the pre-built list was never read
- Stripped spurious `f` prefix from two plain SQL strings: `elo_join = f"""` → `elo_join = """` and `card_rows = db.fetchall(f"""` → `card_rows = db.fetchall("""`

**Verification**
- `python3 -m pyflakes api/main.py` → 0 warnings ✓
- All 5 tested endpoints return 200 ✓

### Notes for next pass
- Consider adding `POST /api/admin/cache-clear` endpoint to allow manual cache invalidation after event imports without full container restart

### Loop Pass 13 — Caching Round 2 & CSS Duplicate Cleanup

**Additional API result caching (`api/main.py`)**
- `/api/leaders/by-base`: 115–130ms → 6ms warm (key: 8 params)
- `/api/players`: 104–117ms → 3ms warm (key: limit, offset, search, meta_id, days)
- `/api/karabast/leaders`: 70–78ms → 3ms warm (key: min_matches)
- All use existing `_LEADERS_TTL = 300s` (5 min); data only changes on event import

**All 5 hot page-load endpoints now cached:**
| Endpoint | Cold | Warm |
|---|---|---|
| `/api/leaders` | 122ms | 5ms |
| `/api/meta-call` | 245ms | 16ms |
| `/api/leaders/by-base` | 130ms | 6ms |
| `/api/players` | 117ms | 3ms |
| `/api/karabast/leaders` | 78ms | 3ms |

**WCAG contrast audit** — all CSS tokens pass AA on both `--bg` and `--surface2`:
- `--text2`: 6.37:1 on bg, 5.50:1 on surface2 ✓
- `--text3`: 7.05:1 on bg, 6.08:1 on surface2 ✓
- `--accent`: 6.12:1 on bg, 5.28:1 on surface2 ✓
- All other tokens: AA or better ✓

**Dead/duplicate CSS removed (`frontend/index.html`)**
- Removed orphaned `.ev-section-header td` + `.ev-upcoming td:first-child` block (~12 lines) that was misplaced inside the card-tile CSS section and was silently overridden by the identical rules in the events section
- Moved `.ev-upcoming td { color: var(--text); }` to the proper events section (rule existed only in the orphaned block)

**Verification**
- All 7 tested endpoints return 200 ✓
- Warm cache confirmed for all 3 new endpoints

### Notes for next pass
- No build/lint pipeline (vanilla JS + FastAPI) — worth adding `eslint` / `pyflakes` to CI
- All 5 heavy page-load endpoints are now cached; remaining work is incremental
