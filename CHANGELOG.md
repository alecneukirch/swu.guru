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

### Notes for next pass
- `mv_card_copy_matrix` materialized view may be stale — verify HMW data is reflected
- No build/lint pipeline exists (vanilla JS + FastAPI) — worth adding `eslint` or `pyflakes` to CI
- Consider creating `mv_card_synergy` to speed up unfiltered synergy queries (currently live CTE, may be slow for leaders with many decklists)
