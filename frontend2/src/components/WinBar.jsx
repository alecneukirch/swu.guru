export default function WinBar({ rate, showPct = true, height = 'h-1.5' }) {
  if (rate == null) return <span className="text-t3 text-xs">—</span>
  const pct = Math.round(rate * 100)
  const color = rate >= 0.55 ? 'bg-win' : rate <= 0.45 ? 'bg-loss' : 'bg-gold'
  return (
    <div className="flex items-center gap-2">
      <div className={`flex-1 bg-surface2 rounded-full overflow-hidden ${height}`}>
        <div className={`${height} ${color} rounded-full transition-all`} style={{ width: `${pct}%` }} />
      </div>
      {showPct && (
        <span className={`font-mono-sw text-xs w-10 text-right ${rate >= 0.55 ? 'text-win' : rate <= 0.45 ? 'text-loss' : 'text-gold'}`}>
          {pct}%
        </span>
      )}
    </div>
  )
}
