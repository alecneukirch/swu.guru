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
