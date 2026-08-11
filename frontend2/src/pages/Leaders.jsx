import { useState, useMemo } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../api.js'
import { useApi } from '../hooks/useApi.js'
import Spinner from '../components/Spinner.jsx'

const SORT_OPTIONS = [
  { value: 'entries',    label: 'Entries' },
  { value: 'win_rate',   label: 'Win Rate' },
  { value: 'top8_rate',  label: 'Top 8 Rate' },
  { value: 'event_wins', label: 'Wins' },
  { value: 'conversion', label: 'Conversion' },
]

const TIER_ORDER  = ['S','A','B','C','D','F']
const TIER_LABEL  = { S:'S Tier', A:'A Tier', B:'B Tier', C:'C Tier', D:'D Tier', F:'F Tier' }
const TIER_COLORS = {
  S: { text: 'text-tier-s', border: 'border-tier-s', bg: 'bg-tier-s/10' },
  A: { text: 'text-tier-a', border: 'border-tier-a', bg: 'bg-tier-a/10' },
  B: { text: 'text-tier-b', border: 'border-tier-b', bg: 'bg-tier-b/10' },
  C: { text: 'text-tier-c', border: 'border-tier-c', bg: 'bg-tier-c/10' },
  D: { text: 'text-tier-d', border: 'border-tier-d', bg: 'bg-tier-d/10' },
  F: { text: 'text-tier-f', border: 'border-tier-f', bg: 'bg-tier-f/10' },
}

const ASP_BORDER = {
  Heroism:    'border-t-asp-heroism',
  Villainy:   'border-t-asp-villainy',
  Cunning:    'border-t-asp-cunning',
  Aggression: 'border-t-asp-aggression',
  Command:    'border-t-asp-command',
  Vigilance:  'border-t-asp-vigilance',
  Force:      'border-t-asp-force',
}

function tierGrade(conv, entries) {
  if (conv == null) return 'C'
  const c = Number(conv)
  const reliable = (entries ?? 0) >= 20
  if (c >= 1.3 && reliable) return 'S'
  if (c >= 1.1 && reliable) return 'A'
  if (c >= 0.9) return 'B'
  if (c >= 0.7) return 'C'
  if (c >= 0.5) return 'D'
  return 'F'
}

export default function Leaders({ filters }) {
  const [sort, setSort]     = useState('entries')
  const [search, setSearch] = useState('')
  const [minEntries, setMinEntries] = useState(10)

  const { data, loading } = useApi(
    () => api.leaders({ ...filters, min_entries: minEntries }),
    [JSON.stringify(filters), minEntries]
  )

  const { leaders, rogues, byTier, total } = useMemo(() => {
    if (!data) return { leaders: [], rogues: [], byTier: {}, total: 0 }
    let rows = (data.leaders ?? data)
    if (search) {
      const q = search.toLowerCase()
      rows = rows.filter(r => r.leader.toLowerCase().includes(q) || (r.base_group ?? '').toLowerCase().includes(q))
    }

    const sorted = [...rows].sort((a, b) => (b[sort] ?? 0) - (a[sort] ?? 0))

    const main   = sorted.filter(r => (r.meta_share ?? 0) >= 0.003 || (r.event_wins ?? 0) > 1)
    const rogues = sorted.filter(r => (r.meta_share ?? 0) <  0.003 && (r.event_wins ?? 0) <= 1)

    const byTier = Object.fromEntries(TIER_ORDER.map(t => [t, []]))
    main.forEach(r => {
      const g = tierGrade(r.conversion, r.entries)
      byTier[g].push(r)
    })

    return { leaders: main, rogues, byTier, total: data.total_entries ?? rows.reduce((s,r)=>s+(r.entries||0),0) }
  }, [data, sort, search])

  const navigate = useNavigate()

  return (
    <div>
      {/* Controls */}
      <div className="flex flex-wrap items-center gap-3 mb-6">
        <input
          type="text"
          placeholder="Search leader or base…"
          value={search}
          onChange={e => setSearch(e.target.value)}
          className="bg-surface border border-border text-t1 text-sm rounded px-3 py-1.5 w-48 focus:outline-none focus:border-border2 placeholder:text-t3"
        />
        <select
          value={sort}
          onChange={e => setSort(e.target.value)}
          className="bg-surface border border-border text-t2 text-sm rounded px-2 py-1.5 focus:outline-none"
        >
          {SORT_OPTIONS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
        </select>
        <label className="flex items-center gap-2 text-t2 text-sm">
          Min entries
          <select
            value={minEntries}
            onChange={e => setMinEntries(Number(e.target.value))}
            className="bg-surface border border-border text-t2 text-sm rounded px-2 py-1.5 focus:outline-none"
          >
            {[5, 10, 20, 50].map(n => <option key={n} value={n}>{n}</option>)}
          </select>
        </label>
        {data && (
          <span className="ml-auto text-t3 text-sm">
            {leaders.length + rogues.length} combos · {total.toLocaleString()} entries
          </span>
        )}
      </div>

      {loading && <Spinner />}

      {/* Tier sections */}
      {!loading && TIER_ORDER.map(tier => {
        const group = byTier[tier]
        if (!group?.length) return null
        const tc = TIER_COLORS[tier]
        return (
          <div key={tier} className="mb-6">
            <div className={`flex items-center gap-3 mb-3 pl-1 border-l-2 ${tc.border}`}>
              <span className={`font-display font-bold text-lg ${tc.text}`}>{tier}</span>
              <span className="text-t3 text-sm">{TIER_LABEL[tier]}</span>
              <span className="text-t3 text-xs ml-auto">{group.length}</span>
            </div>
            <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 xl:grid-cols-6 2xl:grid-cols-7 gap-3">
              {group.map(r => (
                <LeaderCard key={`${r.leader}|${r.base_group}`} row={r} onClick={() => navigate(`/leader/${encodeURIComponent(r.leader)}${r.base_group ? `?base=${encodeURIComponent(r.base_group)}` : ''}`)} />
              ))}
            </div>
          </div>
        )
      })}

      {/* Rogue section */}
      {!loading && rogues.length > 0 && (
        <div className="mb-6">
          <div className="flex items-center gap-3 mb-3 pl-1 border-l-2 border-border2">
            <span className="font-display font-bold text-base text-t2">Rogue</span>
            <span className="text-t3 text-xs">&lt;0.3% meta share</span>
            <span className="text-t3 text-xs ml-auto">{rogues.length}</span>
          </div>
          <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 xl:grid-cols-6 gap-3">
            {rogues.map(r => (
              <LeaderCard key={`${r.leader}|${r.base_group}`} row={r} onClick={() => navigate(`/leader/${encodeURIComponent(r.leader)}`)} />
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

function LeaderCard({ row, onClick }) {
  const mwr      = row.win_rate   != null ? Math.round(row.win_rate * 100)    : null
  const meta     = row.meta_share != null ? (row.meta_share * 100).toFixed(1) : null
  const t8p      = row.top8_rate  != null ? Math.round(row.top8_rate * 100)   : null
  const mwrColor = mwr >= 55 ? 'text-win' : mwr <= 45 ? 'text-loss' : 'text-gold'
  const t8Color  = t8p >= 20 ? 'text-win' : t8p >= 12 ? 'text-gold' : 'text-t2'
  const aspBorder = ASP_BORDER[row.primary_aspect] ?? 'border-t-border2'
  const imgUrl   = `/api/cards/leader-image/${encodeURIComponent(row.leader)}`

  return (
    <button
      onClick={onClick}
      className={`group relative bg-surface hover:bg-surface2 border border-border hover:border-border2 border-t-2 ${aspBorder} rounded-lg overflow-hidden text-left transition-all`}
      style={{ aspectRatio: '1 / 1' }}
    >
      {/* Card art */}
      <img
        src={imgUrl}
        alt={row.leader}
        onError={e => { e.target.style.display = 'none' }}
        className="absolute inset-0 w-full h-full object-cover scale-125 group-hover:scale-[1.35] transition-transform duration-300"
        style={{ objectPosition: 'center 15%' }}
      />

      {/* Strong gradient from bottom covering ~55% of card */}
      <div className="absolute inset-0 bg-gradient-to-t from-black/95 via-black/60 to-transparent pointer-events-none" />

      {/* Meta % badge top-right */}
      {meta != null && (
        <div className="absolute top-1.5 right-1.5 bg-black/70 text-gold text-[14px] font-mono rounded px-1 py-0.5 leading-none z-10">
          {meta}%
        </div>
      )}

      {/* Name + stats pinned to bottom */}
      <div className="absolute bottom-0 left-0 right-0 px-2 pb-2 pt-1 z-10">
        <div className="mb-1.5 drop-shadow">
          <div className="font-display font-bold text-[16px] text-white leading-tight truncate">
            {row.leader.split(', ')[0]}
          </div>
          <div className="font-display text-[12px] text-t2 leading-tight truncate">
            {row.leader.split(', ').slice(1).join(', ')}
          </div>
          {row.base_group && (
            <div className="font-mono text-[10px] text-gold/80 leading-tight truncate mt-0.5">
              {row.base_group}
            </div>
          )}
        </div>
        <div className="grid grid-cols-4 gap-0.5">
          <StatCell label="Decks" value={row.entries}                        color="text-t1" />
          <StatCell label="T8"    value={t8p  != null ? `${t8p}%`  : '—'}   color={t8Color} />
          <StatCell label="Wins"  value={row.event_wins ?? '—'}              color="text-t2" />
          <StatCell label="MWR"   value={mwr  != null ? `${mwr}%`  : '—'}   color={mwrColor} />
        </div>
      </div>
    </button>
  )
}

function StatCell({ label, value, color = 'text-t2' }) {
  return (
    <div className="text-center">
      <div className="text-t3 text-[11px] uppercase tracking-wide leading-none mb-0.5">{label}</div>
      <div className={`font-mono text-[14px] font-semibold ${color}`}>{value}</div>
    </div>
  )
}
