import { useState } from 'react'
import { useParams, useSearchParams, Link } from 'react-router-dom'
import { api } from '../api.js'
import { useApi } from '../hooks/useApi.js'
import Spinner from '../components/Spinner.jsx'
import WinBar from '../components/WinBar.jsx'
import LeaderImage from '../components/LeaderImage.jsx'

const TABS = ['Cards', 'Matchups', 'Weaknesses', 'Mirror']

const TIER_COLORS = {
  S: 'text-tier-s border-tier-s bg-tier-s/10',
  A: 'text-tier-a border-tier-a bg-tier-a/10',
  B: 'text-tier-b border-tier-b bg-tier-b/10',
  C: 'text-tier-c border-tier-c bg-tier-c/10',
  D: 'text-tier-d border-tier-d bg-tier-d/10',
  F: 'text-tier-f border-tier-f bg-tier-f/10',
}

function tierGrade(conv) {
  if (conv == null) return null
  if (conv >= 1.3) return 'S'
  if (conv >= 1.1) return 'A'
  if (conv >= 0.9) return 'B'
  if (conv >= 0.7) return 'C'
  if (conv >= 0.5) return 'D'
  return 'F'
}

export default function LeaderDetail({ filters }) {
  const { name } = useParams()
  const [searchParams] = useSearchParams()
  const leader = decodeURIComponent(name)
  const selectedBase = searchParams.get('base')
  const [tab, setTab] = useState('Cards')

  const { data: stats } = useApi(
    () => api.leaderStats(leader, filters),
    [leader, JSON.stringify(filters)]
  )

  const namePart     = leader.split(', ')[0]
  const subtitlePart = leader.split(', ').slice(1).join(', ')
  const tier         = stats ? tierGrade(stats.conversion != null ? Number(stats.conversion) : null) : null
  const tierCls      = tier ? TIER_COLORS[tier] : null

  return (
    <div>
      {/* Header */}
      <div className="mb-6">
        <Link to="/" className="text-t3 hover:text-t1 text-sm mb-4 inline-block">← Leaders</Link>

        <div className="flex items-start gap-5">
          {/* Portrait thumbnail */}
          <div className="w-24 h-24 rounded-lg overflow-hidden bg-surface flex-shrink-0 border border-border">
            <LeaderImage
              leader={leader}
              className="w-full h-full object-cover"
              style={{ objectPosition: 'center 15%' }}
            />
          </div>

          <div className="flex-1 min-w-0">
            {/* Name + tier badge */}
            <div className="flex items-center gap-3 flex-wrap mb-0.5">
              <h1 className="font-display font-bold text-4xl text-t1 leading-tight">{namePart}</h1>
              {tier && (
                <span className={`font-display font-bold text-base px-2 py-0.5 rounded border ${tierCls}`}>
                  {tier}
                  {stats?.conversion != null && (
                    <span className="font-mono font-normal text-sm ml-1 opacity-90">
                      {Number(stats.conversion).toFixed(2)}×
                    </span>
                  )}
                </span>
              )}
            </div>
            <div className="text-t2 text-sm mb-3">
              {subtitlePart}
              {selectedBase && <span className="text-gold/80 font-mono ml-3">{selectedBase}</span>}
            </div>

            {/* Stats row */}
            {stats && (
              <>
                <div className="flex flex-wrap gap-5 mb-3">
                  <Stat label="Decklists"  value={stats.entries?.toLocaleString()} />
                  <Stat label="Meta"       value={stats.meta_share != null ? `${(stats.meta_share * 100).toFixed(1)}%` : '—'} />
                  <Stat label="T8 Rate"    value={stats.top8_rate != null ? `${Math.round(stats.top8_rate * 100)}%` : '—'}
                        color={stats.top8_rate >= 0.2 ? 'text-win' : stats.top8_rate >= 0.12 ? 'text-gold' : 'text-t2'} />
                  <Stat label="Event Wins" value={stats.event_wins ?? '—'} />
                  <Stat label="MWR"        value={stats.win_rate != null ? `${Math.round(stats.win_rate * 100)}%` : '—'} color={wr(stats.win_rate)} />
                  <Stat label="GWR"        value={stats.game_win_rate != null ? `${Math.round(stats.game_win_rate * 100)}%` : '—'} />
                </div>
                {/* Conversion funnel */}
                {(stats.t50_conv || stats.t25_conv || stats.t10_conv || stats.t1_conv) && (
                  <div className="flex gap-4">
                    <FunnelStat label="T50" value={stats.t50_conv} />
                    <FunnelStat label="T25" value={stats.t25_conv} />
                    <FunnelStat label="T10" value={stats.t10_conv} />
                    <FunnelStat label="T1"  value={stats.t1_conv} />
                  </div>
                )}
              </>
            )}
          </div>
        </div>
      </div>

      {/* Tabs */}
      <div className="flex gap-1 border-b border-border mb-6">
        {TABS.map(t => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={`px-4 py-2 text-sm font-display font-semibold tracking-wide transition-colors
              ${tab === t
                ? 'text-gold border-b-2 border-gold -mb-px'
                : 'text-t2 hover:text-t1'}`}
          >
            {t}
          </button>
        ))}
      </div>

      {tab === 'Cards'      && <CardsTab leader={leader} filters={filters} />}
      {tab === 'Matchups'   && <MatchupsTab leader={leader} filters={filters} />}
      {tab === 'Weaknesses' && <WeaknessesTab leader={leader} filters={filters} />}
      {tab === 'Mirror'     && <MirrorTab leader={leader} filters={filters} />}
    </div>
  )
}

function Stat({ label, value, color }) {
  return (
    <div>
      <div className="text-t3 text-xs uppercase tracking-wide">{label}</div>
      <div className={`font-mono-sw text-base font-semibold ${color ?? 'text-t1'}`}>{value}</div>
    </div>
  )
}

function wr(r) {
  if (r == null) return 'text-t3'
  return r >= 0.55 ? 'text-win' : r <= 0.45 ? 'text-loss' : 'text-gold'
}

function FunnelStat({ label, value }) {
  if (value == null) return null
  const v = Number(value)
  const color = v >= 1.3 ? 'text-tier-s' : v >= 1.1 ? 'text-tier-a' : v >= 0.9 ? 'text-tier-b' : v >= 0.7 ? 'text-tier-c' : 'text-loss'
  return (
    <div className="text-center">
      <div className="text-t3 text-[10px] uppercase tracking-wide leading-none mb-0.5">{label}</div>
      <div className={`font-mono-sw text-sm font-semibold ${color}`}>{v.toFixed(2)}×</div>
    </div>
  )
}

// ── Cards tab ────────────────────────────────────────────────────────────────

function CardsTab({ leader, filters }) {
  const [showSideboard, setShowSideboard] = useState(false)
  const { data, loading } = useApi(
    () => api.leaderCards(leader, filters),
    [leader, JSON.stringify(filters)]
  )

  if (loading) return <Spinner />

  const leaderFirst = leader.split(', ')[0].toLowerCase()
  const isLeaderCard = r => r.card_name.toLowerCase().startsWith(leaderFirst) && r.avg_copies <= 1 && r.copy_rate >= 0.99
  const main = (data ?? []).filter(r => !r.is_sideboard && !isLeaderCard(r))
  const side = (data ?? []).filter(r => r.is_sideboard && !isLeaderCard(r))

  return (
    <div>
      <div className="flex items-center gap-3 mb-4">
        <button
          onClick={() => setShowSideboard(false)}
          className={`text-sm font-display font-semibold px-3 py-1 rounded transition-colors ${!showSideboard ? 'bg-gold/20 text-gold' : 'text-t2 hover:text-t1'}`}
        >
          Main Deck ({main.length})
        </button>
        <button
          onClick={() => setShowSideboard(true)}
          className={`text-sm font-display font-semibold px-3 py-1 rounded transition-colors ${showSideboard ? 'bg-gold/20 text-gold' : 'text-t2 hover:text-t1'}`}
        >
          Sideboard ({side.length})
        </button>
      </div>
      <CardGrid rows={showSideboard ? side : main} />
    </div>
  )
}

const CARD_SECTIONS = [
  { label: 'Ground Units', filter: r => r.type === 'Unit' && r.arena === 'Ground' },
  { label: 'Space Units',  filter: r => r.type === 'Unit' && r.arena === 'Space'  },
  { label: 'Events',       filter: r => r.type === 'Event'   },
  { label: 'Upgrades',     filter: r => r.type === 'Upgrade' },
  { label: 'Other',        filter: r => !['Unit','Event','Upgrade'].includes(r.type) },
]

function CardGrid({ rows }) {
  const byCost = (a, b) => (a.cost ?? 99) - (b.cost ?? 99) || a.card_name.localeCompare(b.card_name)

  return (
    <div className="space-y-8">
      {CARD_SECTIONS.map(({ label, filter }) => {
        const section = rows.filter(filter).sort(byCost)
        if (!section.length) return null
        return (
          <div key={label}>
            <div className="flex items-center gap-3 mb-3 pl-1 border-l-2 border-border2">
              <span className="font-display font-bold text-base text-t1">{label}</span>
              <span className="text-t3 text-xs">{section.length}</span>
            </div>
            <div className="grid grid-cols-3 sm:grid-cols-4 md:grid-cols-5 lg:grid-cols-6 xl:grid-cols-7 2xl:grid-cols-8 gap-3">
              {section.map(r => <CardTile key={r.card_name} row={r} />)}
            </div>
          </div>
        )
      })}
    </div>
  )
}

function CardTile({ row }) {
  const inc     = row.copy_rate  != null ? Math.round(row.copy_rate * 100) : null
  const mwr     = row.card_mwr   != null ? Math.round(row.card_mwr * 100)  : null
  const copies  = row.avg_copies != null ? row.avg_copies.toFixed(2) : null
  const mwrColor = mwr == null ? 'text-t3' : mwr >= 55 ? 'text-win' : mwr <= 45 ? 'text-loss' : 'text-gold'
  const imgUrl   = `/api/cards/card-image/${encodeURIComponent(row.card_name)}`
  const displayName = row.card_name.includes(' | ')
    ? row.card_name.split(' | ')[0]
    : row.card_name.split(', ')[0]

  return (
    <div className="group relative bg-surface border border-border rounded-lg overflow-hidden" style={{ aspectRatio: '2/3' }}>
      {/* Card art */}
      <img
        src={imgUrl}
        alt={row.card_name}
        onError={e => { e.target.style.display = 'none' }}
        className="absolute inset-0 w-full h-full object-cover group-hover:scale-105 transition-transform duration-300"
      />
      <div className="absolute inset-0 bg-gradient-to-t from-black/95 via-black/20 to-transparent pointer-events-none" />

      {/* Cost badge top-left */}
      {row.cost != null && (
        <div className="absolute top-1 left-1 bg-black/80 text-t1 font-bold font-mono text-[13px] rounded-full w-6 h-6 flex items-center justify-center leading-none">
          {row.cost}
        </div>
      )}

      {/* Inclusion badge top-right */}
      {inc != null && (
        <div className="absolute top-1 right-1 bg-black/75 text-gold font-mono text-[11px] rounded px-1 py-0.5 leading-none">
          {inc}%
        </div>
      )}

      {/* Name + stats at bottom */}
      <div className="absolute bottom-0 left-0 right-0 px-1.5 pb-1.5 pt-1">
        <div className="font-display font-semibold text-[12px] text-white leading-tight truncate mb-1">
          {displayName}
        </div>
        <div className="grid grid-cols-2 gap-0.5">
          <div className="text-center">
            <div className="text-t3 text-[9px] uppercase leading-none mb-0.5">Copies</div>
            <div className="font-mono-sw text-[11px] text-t1 font-semibold">{copies ?? '—'}</div>
          </div>
          <div className="text-center">
            <div className="text-t3 text-[9px] uppercase leading-none mb-0.5">MWR</div>
            <div className={`font-mono-sw text-[11px] font-semibold ${mwrColor}`}>{mwr != null ? `${mwr}%` : '—'}</div>
          </div>
        </div>
      </div>
    </div>
  )
}

// ── Matchups tab ─────────────────────────────────────────────────────────────

function MatchupsTab({ leader, filters }) {
  const { data, loading } = useApi(
    () => api.leaderMatchups(leader, filters),
    [leader, JSON.stringify(filters)]
  )
  if (loading) return <Spinner />
  const rows = [...(data ?? [])].sort((a, b) => (b.win_rate ?? 0) - (a.win_rate ?? 0))

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead className="border-b border-border">
          <tr>
            <th className="px-3 py-2 text-left text-xs uppercase tracking-wide text-t3">Opponent</th>
            <th className="px-3 py-2 text-left text-xs uppercase tracking-wide text-t3">Win Rate</th>
            <th className="px-3 py-2 text-left text-xs uppercase tracking-wide text-t3">Record</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(r => (
            <tr key={r.opponent} className="border-b border-border/50 hover:bg-surface/50 transition-colors">
              <td className="px-3 py-2 text-t1">{r.opponent}</td>
              <td className="px-3 py-2 w-48"><WinBar rate={r.win_rate} /></td>
              <td className="px-3 py-2 font-mono-sw text-t3 text-xs">{r.wins}–{r.losses}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

// ── Weaknesses tab ───────────────────────────────────────────────────────────

function WeaknessesTab({ leader, filters }) {
  const { data, loading } = useApi(
    () => api.leaderWeaknesses(leader, filters),
    [leader, JSON.stringify(filters)]
  )
  if (loading) return <Spinner />
  const rows = data ?? []

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead className="border-b border-border">
          <tr>
            <th className="px-3 py-2 text-left text-xs uppercase tracking-wide text-t3">Card</th>
            <th className="px-3 py-2 text-left text-xs uppercase tracking-wide text-t3">Opponent WR when played</th>
            <th className="px-3 py-2 text-left text-xs uppercase tracking-wide text-t3">Δ vs baseline</th>
            <th className="px-3 py-2 text-left text-xs uppercase tracking-wide text-t3">Games</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(r => (
            <tr key={r.card_name} className="border-b border-border/50 hover:bg-surface/50 transition-colors">
              <td className="px-3 py-2 text-t1">{r.card_name}</td>
              <td className="px-3 py-2 w-48"><WinBar rate={r.opp_win_rate} /></td>
              <td className="px-3 py-2 font-mono-sw text-xs">
                <Delta val={r.delta} />
              </td>
              <td className="px-3 py-2 font-mono-sw text-t3 text-xs">{r.games}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

// ── Mirror tab ───────────────────────────────────────────────────────────────

function MirrorTab({ leader, filters }) {
  const { data, loading } = useApi(
    () => api.leaderMirror(leader, filters),
    [leader, JSON.stringify(filters)]
  )
  if (loading) return <Spinner />
  const rows = data ?? []

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead className="border-b border-border">
          <tr>
            <th className="px-3 py-2 text-left text-xs uppercase tracking-wide text-t3">Card</th>
            <th className="px-3 py-2 text-left text-xs uppercase tracking-wide text-t3">WR in mirror</th>
            <th className="px-3 py-2 text-left text-xs uppercase tracking-wide text-t3">Δ vs mirror avg</th>
            <th className="px-3 py-2 text-left text-xs uppercase tracking-wide text-t3">Games</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(r => (
            <tr key={r.card_name} className="border-b border-border/50 hover:bg-surface/50 transition-colors">
              <td className="px-3 py-2 text-t1">{r.card_name}</td>
              <td className="px-3 py-2 w-48"><WinBar rate={r.win_rate} /></td>
              <td className="px-3 py-2 font-mono-sw text-xs"><Delta val={r.delta} /></td>
              <td className="px-3 py-2 font-mono-sw text-t3 text-xs">{r.games}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function Delta({ val }) {
  if (val == null) return <span className="text-t3">—</span>
  const sign = val > 0 ? '+' : ''
  const cls = val > 0.02 ? 'text-win' : val < -0.02 ? 'text-loss' : 'text-t2'
  return <span className={cls}>{sign}{(val * 100).toFixed(1)}pp</span>
}
