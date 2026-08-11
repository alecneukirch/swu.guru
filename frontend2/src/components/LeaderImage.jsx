import { useState } from 'react'

const FALLBACK = 'data:image/svg+xml,<svg xmlns="http://www.w3.org/2000/svg"/>'

export default function LeaderImage({ leader, className = '' }) {
  const [failed, setFailed] = useState(false)
  const src = failed ? FALLBACK : `/api/cards/leader-image/${encodeURIComponent(leader)}`
  return (
    <img
      src={src}
      alt={leader}
      onError={() => setFailed(true)}
      className={`object-cover ${className}`}
    />
  )
}
