import { useState, useEffect, useRef } from 'react'

export function useApi(fetcher, deps = []) {
  const [data, setData]     = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError]   = useState(null)
  const abortRef = useRef(null)

  useEffect(() => {
    abortRef.current?.abort()
    abortRef.current = new AbortController()

    setLoading(true)
    setError(null)

    fetcher()
      .then(d => { setData(d); setLoading(false) })
      .catch(e => { if (e.name !== 'AbortError') { setError(e); setLoading(false) } })

    return () => abortRef.current?.abort()
  }, deps)

  return { data, loading, error }
}
