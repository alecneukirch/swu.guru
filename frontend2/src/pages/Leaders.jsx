import { useState, useMemo } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../api.js'
import { useApi } from '../hooks/useApi.js'
import Spinner from '../components/Spinner.jsx'
import WinBar from '../components/WinBar.jsx'
import LeaderImage from '../components/LeaderImage.jsx'

const SORT_OPTIONS = [
  { value: 'entries',   label: 'Entries' },
  { value: 'win_rate',  label: 'Win Rate' },
  { value: 'top8_rate', label: 'Top 8 Rate' },
  { value: 'wins',      label: 'Wins' },
]

export default function Leaders({ filters }) {
  const [sort, setSort]     = useState('entries')
  const [search, setSearch] = useState('')
  const [minEntries, setMinEntries] = useState(10)

  const { data, loading } = useApi(
    () => api.leaders({ ...filters, min_entries: minEntries }),
    [JSON.stringify(filters), minEntries]
  )

  const leaders = useMemo(() => {
    if (!data) return []
    let rows = data.leaders ?? data
    if (search) rows = rows.filter(r => r.leader.toLowerCase().includes(search.toLowerCase()))
    return [...rows].sort((a, b) => (b[sort] ?? 0) - (a[sort] ?? 0))
  }, [data, sort, search])

  const navigate = useNavigate()

  return (
    <div>
      {/* Controls */}
      <div className="flex flex-wrap items-center gap-3 mb-6">
        <input
          type="text"
          placeholder="Search leaders…"
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
            {leaders.length} leaders · {(data.total_entries ?? 0).toLocaleString()} entries
          </span>
        )}
      </div>

      {loading && <Spinner />}

      {/* Grid */}
      <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 xl:grid-cols-6 gap-3">
        {leaders.map(r => (
          <LeaderCard key={r.leader} row={r} onClick={() => navigate(`/leader/${encodeURIComponent(r.leader)}`)} />
        ))}
      </div>
    </div>
  )
}

function LeaderCard({ row, onClick }) {
  const mwr = row.win_rate != null ? Math.round(row.win_rate * 100) : null
  const gwr = row.game_win_rate != null ? Math.round(row.game_win_rate * 100) : null
  const metaPct = row.meta_share != null ? (row.meta_share * 100).toFixed(1) : null
  const top8Pct = row.top8_rate != null ? Math.round(row.top8_rate * 100) : null
  const winColor = row.win_rate >= 0.55 ? 'text-win' : row.win_rate <= 0.45 ? 'text-loss' : 'text-gold'

  return (
    <button
      onClick={onClick}
      className="group relative bg-surface hover:bg-surface2 border border-border hover:border-border2 rounded-lg overflow-hidden text-left transition-all"
    >
      {/* Rank badge */}
      <div className="absolute top-1.5 left-1.5 z-10 bg-black/60 text-t3 text-[10px] font-mono-sw rounded px-1 leading-4">
        #{row.rank}
      </div>
      {/* Meta share badge */}
      {metaPct != null && (
        <div className="absolute top-1.5 right-1.5 z-10 bg-black/60 text-gold text-[10px] font-mono-sw rounded px-1 leading-4">
          {metaPct}%
        </div>
      )}
      <div className="aspect-[5/7] overflow-hidden bg-bg2">
        <LeaderImage
          leader={row.leader}
          className="w-full h-full object-cover group-hover:scale-105 transition-transform duration-300"
        />
      </div>
      <div className="p-2">
        <div className="font-display font-semibold text-sm text-t1 leading-tight mb-1 truncate">
          {row.leader}
        </div>
        {/* MWR / GWR row */}
        <div className="flex items-center justify-between mb-1">
          <span className="text-t3 text-[10px] font-mono-sw">{row.entries} entries</span>
          {top8Pct != null && (
            <span className="text-t3 text-[10px] font-mono-sw">T8: {top8Pct}%</span>
          )}
        </div>
        <div className="flex items-center justify-between mb-1">
          {mwr != null && (
            <span className="text-[10px] font-mono-sw">
              <span className="text-t3">MWR </span>
              <span className={`font-semibold ${winColor}`}>{mwr}%</span>
            </span>
          )}
          {gwr != null && (
            <span className="text-[10px] font-mono-sw">
              <span className="text-t3">GWR </span>
              <span className="text-t2 font-semibold">{gwr}%</span>
            </span>
          )}
        </div>
        <WinBar rate={row.win_rate} showPct={false} height="h-1" />
      </div>
    </button>
  )
}
