import { useState } from 'react'
import { useParams, Link } from 'react-router-dom'
import { api } from '../api.js'
import { useApi } from '../hooks/useApi.js'
import Spinner from '../components/Spinner.jsx'
import WinBar from '../components/WinBar.jsx'
import LeaderImage from '../components/LeaderImage.jsx'

const TABS = ['Cards', 'Matchups', 'Weaknesses', 'Mirror']

export default function LeaderDetail({ filters }) {
  const { name } = useParams()
  const leader = decodeURIComponent(name)
  const [tab, setTab] = useState('Cards')

  const { data: stats } = useApi(
    () => api.leaderStats(leader, filters),
    [leader, JSON.stringify(filters)]
  )

  return (
    <div>
      {/* Header */}
      <div className="flex items-start gap-4 mb-6">
        <Link to="/" className="text-t3 hover:text-t1 text-sm mt-1">← Leaders</Link>
        <div className="w-20 h-28 rounded-lg overflow-hidden bg-surface flex-shrink-0">
          <LeaderImage leader={leader} className="w-full h-full" />
        </div>
        <div className="flex-1 min-w-0">
          <h1 className="font-display font-bold text-2xl text-t1 leading-tight">{leader}</h1>
          {stats && (
            <div className="flex flex-wrap gap-4 mt-2">
              <Stat label="Entries"  value={stats.entries} />
              <Stat label="Win Rate" value={stats.win_rate != null ? `${Math.round(stats.win_rate * 100)}%` : '—'} color={wr(stats.win_rate)} />
              <Stat label="Top 8"    value={stats.top8s ?? '—'} />
              <Stat label="Wins"     value={stats.wins ?? '—'} />
              <Stat label="Meta %"   value={stats.meta_share != null ? `${(stats.meta_share * 100).toFixed(1)}%` : '—'} />
            </div>
          )}
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

// ── Cards tab ────────────────────────────────────────────────────────────────

function CardsTab({ leader, filters }) {
  const [showSideboard, setShowSideboard] = useState(false)
  const { data, loading } = useApi(
    () => api.leaderCards(leader, filters),
    [leader, JSON.stringify(filters)]
  )

  if (loading) return <Spinner />

  const main = (data ?? []).filter(r => !r.is_sideboard)
  const side = (data ?? []).filter(r => r.is_sideboard)

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
      <CardTable rows={showSideboard ? side : main} />
    </div>
  )
}

function CardTable({ rows }) {
  const [sort, setSort] = useState('copy_rate')

  const sorted = [...rows].sort((a, b) => (b[sort] ?? 0) - (a[sort] ?? 0))

  const th = (key, label) => (
    <th
      key={key}
      onClick={() => setSort(key)}
      className={`px-3 py-2 text-left text-xs uppercase tracking-wide cursor-pointer select-none transition-colors
        ${sort === key ? 'text-gold' : 'text-t3 hover:text-t2'}`}
    >
      {label}
    </th>
  )

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead className="border-b border-border">
          <tr>
            {th('card_name',  'Card')}
            {th('copy_rate',  'Inclusion')}
            {th('avg_copies', 'Avg Copies')}
            {th('card_mwr',   'MWR')}
          </tr>
        </thead>
        <tbody>
          {sorted.map((r, i) => (
            <tr key={r.card_name} className={`border-b border-border/50 hover:bg-surface/50 transition-colors`}>
              <td className="px-3 py-2 text-t1 font-medium">{r.card_name}</td>
              <td className="px-3 py-2 w-40">
                <WinBar rate={r.copy_rate} />
              </td>
              <td className="px-3 py-2 font-mono-sw text-t2 text-xs">
                {r.avg_copies?.toFixed(2) ?? '—'}
              </td>
              <td className="px-3 py-2 w-36">
                {r.card_mwr != null
                  ? <WinBar rate={r.card_mwr} />
                  : <span className="text-t3 text-xs">—</span>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
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
