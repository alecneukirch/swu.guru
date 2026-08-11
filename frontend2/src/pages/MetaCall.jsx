import { useNavigate } from 'react-router-dom'
import { api } from '../api.js'
import { useApi } from '../hooks/useApi.js'
import Spinner from '../components/Spinner.jsx'
import WinBar from '../components/WinBar.jsx'
import LeaderImage from '../components/LeaderImage.jsx'

export default function MetaCall({ filters }) {
  const { data, loading } = useApi(
    () => api.metaCall(filters),
    [JSON.stringify(filters)]
  )
  const navigate = useNavigate()

  if (loading) return <Spinner />
  const rows = data ?? []

  return (
    <div>
      <div className="mb-6">
        <h1 className="font-display font-bold text-xl text-t1">Meta Call</h1>
        <p className="text-t3 text-sm mt-1">
          Score = win rate weighted by how often you'd face each opponent at their meta share.
        </p>
      </div>

      <div className="grid gap-3">
        {rows.map((r, i) => (
          <button
            key={r.leader}
            onClick={() => navigate(`/leader/${encodeURIComponent(r.leader)}`)}
            className="flex items-center gap-4 bg-surface hover:bg-surface2 border border-border hover:border-border2 rounded-lg p-3 text-left transition-all"
          >
            <span className="font-mono-sw text-t3 text-sm w-6 text-right flex-shrink-0">
              {i + 1}
            </span>
            <div className="w-12 h-16 rounded overflow-hidden bg-bg2 flex-shrink-0">
              <LeaderImage leader={r.leader} className="w-full h-full object-cover" />
            </div>
            <div className="flex-1 min-w-0">
              <div className="font-display font-semibold text-t1">{r.leader}</div>
              <div className="flex gap-4 mt-1">
                <StatMini label="Score"   value={r.score != null ? r.score.toFixed(3) : '—'} />
                <StatMini label="WR"      value={r.win_rate != null ? `${Math.round(r.win_rate * 100)}%` : '—'} />
                <StatMini label="Entries" value={r.entries} />
                <StatMini label="Meta %"  value={r.meta_share != null ? `${(r.meta_share * 100).toFixed(1)}%` : '—'} />
              </div>
            </div>
            <div className="w-32 flex-shrink-0">
              <WinBar rate={r.score != null ? r.score / (rows[0]?.score ?? 1) : null} showPct={false} height="h-2" />
              {r.score != null && (
                <div className="text-right font-mono-sw text-gold text-xs mt-0.5">
                  {r.score.toFixed(3)}
                </div>
              )}
            </div>
          </button>
        ))}
      </div>
    </div>
  )
}

function StatMini({ label, value }) {
  return (
    <div>
      <div className="text-t3 text-xs">{label}</div>
      <div className="font-mono-sw text-t1 text-sm">{value ?? '—'}</div>
    </div>
  )
}
