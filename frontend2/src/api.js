const BASE = '/api'

async function get(path, params = {}) {
  const url = new URL(BASE + path, window.location.origin)
  Object.entries(params).forEach(([k, v]) => v != null && url.searchParams.set(k, v))
  const r = await fetch(url)
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`)
  return r.json()
}

export const api = {
  summary:      (p) => get('/summary', p),
  leaders:      (p) => get('/leaders', p),
  leaderStats:  (name, p) => get(`/leader/${encodeURIComponent(name)}/stats`, p),
  leaderCards:  (name, p) => get(`/leader/${encodeURIComponent(name)}/cards`, p),
  leaderMatchups: (name, p) => get(`/leader/${encodeURIComponent(name)}/matchups`, p),
  leaderWeaknesses: (name, p) => get(`/leader/${encodeURIComponent(name)}/weaknesses`, p),
  leaderMirror: (name, p) => get(`/leader/${encodeURIComponent(name)}/mirror`, p),
  matrix:       (p) => get('/matchups', p),
  metaCall:     (p) => get('/meta-call', p),
  metas:        () => get('/metas/dropdown'),
}
