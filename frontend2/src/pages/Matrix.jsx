import { useMemo } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../api.js'
import { useApi } from '../hooks/useApi.js'
import Spinner from '../components/Spinner.jsx'

function cell(rate, games) {
  if (rate == null || games < 5) return { bg: 'bg-surface', text: 'text-t3', label: '—' }
  const pct = Math.round(rate * 100)
  const bg = rate >= 0.60 ? 'bg-win/25'
           : rate >= 0.55 ? 'bg-win/12'
           : rate >= 0.52 ? 'bg-win/6'
           : rate <= 0.40 ? 'bg-loss/25'
           : rate <= 0.45 ? 'bg-loss/12'
           : rate <= 0.48 ? 'bg-loss/6'
           : 'bg-transparent'
  const text = rate >= 0.55 ? 'text-win'
             : rate <= 0.45 ? 'text-loss'
             : 'text-t2'
  return { bg, text, label: `${pct}%` }
}

export default function Matrix({ filters }) {
  const { data, loading } = useApi(
    () => api.matrix(filters),
    [JSON.stringify(filters)]
  )
  const navigate = useNavigate()

  const { leaders, lookup } = useMemo(() => {
    if (!data) return { leaders: [], lookup: {} }
    const leaders = data.leaders ?? []
    const lookup = {}
    for (const r of data.matchups ?? []) {
      lookup[`${r.leader}|||${r.opponent}`] = r
    }
    return { leaders, lookup }
  }, [data])

  if (loading) return <Spinner />

  return (
    <div>
      <div className="mb-4">
        <h1 className="font-display font-bold text-xl text-t1">Matchup Matrix</h1>
        <p className="text-t3 text-sm mt-1">Row leader win rate vs column leader. Min 5 matches shown.</p>
      </div>
      <div className="overflow-auto">
        <table className="text-xs border-collapse">
          <thead>
            <tr>
              <th className="sticky left-0 z-10 bg-bg w-32 min-w-32 text-t3 font-normal px-2 py-1 text-right border-b border-r border-border">vs →</th>
              {leaders.map(opp => (
                <th
                  key={opp}
                  className="bg-bg px-1 py-2 text-t3 font-normal border-b border-border"
                  style={{ writingMode: 'vertical-rl', transform: 'rotate(180deg)', height: 80, whiteSpace: 'nowrap' }}
                >
                  {opp}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {leaders.map(leader => (
              <tr key={leader} className="hover:bg-surface/30 group">
                <td
                  className="sticky left-0 z-10 bg-bg group-hover:bg-surface/30 px-2 py-1 text-t1 font-medium border-r border-b border-border cursor-pointer hover:text-gold transition-colors truncate max-w-32"
                  onClick={() => navigate(`/leader/${encodeURIComponent(leader)}`)}
                >
                  {leader}
                </td>
                {leaders.map(opp => {
                  if (leader === opp) {
                    return <td key={opp} className="border border-border/30 bg-surface/50 w-12 text-center text-t3">—</td>
                  }
                  const m = lookup[`${leader}|||${opp}`]
                  const c = cell(m?.win_rate, m?.games ?? 0)
                  return (
                    <td
                      key={opp}
                      className={`border border-border/30 ${c.bg} w-12 text-center font-mono-sw ${c.text} cursor-default`}
                      title={m ? `${leader} vs ${opp}: ${m.wins}–${m.losses} (${m.games} games)` : ''}
                    >
                      {c.label}
                    </td>
                  )
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
