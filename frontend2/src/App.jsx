import React, { useState, useEffect } from 'react'
import { Routes, Route, NavLink, useNavigate } from 'react-router-dom'
import Leaders from './pages/Leaders.jsx'
import LeaderDetail from './pages/LeaderDetail.jsx'
import Matrix from './pages/Matrix.jsx'
import MetaCall from './pages/MetaCall.jsx'
import { api } from './api.js'

function NavBtn({ to, children }) {
  return (
    <NavLink
      to={to}
      className={({ isActive }) =>
        `px-4 py-2 text-sm font-display font-semibold tracking-wide uppercase transition-colors rounded
         ${isActive
           ? 'text-gold border-b-2 border-gold'
           : 'text-t2 hover:text-t1'}`
      }
    >
      {children}
    </NavLink>
  )
}

export default function App() {
  const [metas, setMetas] = useState([])
  const [filters, setFilters] = useState({ meta_id: null, event_level: null })

  useEffect(() => {
    api.metas().then(setMetas).catch(() => {})
  }, [])

  return (
    <div className="min-h-screen bg-bg font-body text-t1">
      {/* ── Nav ── */}
      <header className="sticky top-0 z-40 bg-bg/95 backdrop-blur border-b border-border">
        <div className="max-w-[1800px] mx-auto px-4 h-14 flex items-center gap-2">
          <span className="font-display font-bold text-xl text-gold tracking-widest mr-4">
            SWU.GURU
          </span>
          <nav className="flex items-center gap-1">
            <NavBtn to="/">Leaders</NavBtn>
            <NavBtn to="/matrix">Matrix</NavBtn>
            <NavBtn to="/meta">Meta Call</NavBtn>
          </nav>
          <div className="ml-auto flex items-center gap-2">
            <FilterBar metas={metas} filters={filters} onChange={setFilters} />
          </div>
        </div>
      </header>

      {/* ── Pages ── */}
      <main className="max-w-[1800px] mx-auto px-4 py-6">
        <Routes>
          <Route path="/"             element={<Leaders filters={filters} />} />
          <Route path="/leader/:name" element={<LeaderDetail filters={filters} />} />
          <Route path="/matrix"       element={<Matrix filters={filters} />} />
          <Route path="/meta"         element={<MetaCall filters={filters} />} />
        </Routes>
      </main>
    </div>
  )
}

function FilterBar({ metas, filters, onChange }) {
  return (
    <div className="flex items-center gap-2">
      <select
        value={filters.meta_id ?? ''}
        onChange={e => onChange(f => ({ ...f, meta_id: e.target.value || null }))}
        className="bg-surface border border-border text-t2 text-xs rounded px-2 py-1.5 focus:outline-none focus:border-border2"
      >
        <option value="">All metas</option>
        {metas.map(m => (
          <option key={m.id} value={m.id}>{m.label}</option>
        ))}
      </select>
      <select
        value={filters.event_level ?? ''}
        onChange={e => onChange(f => ({ ...f, event_level: e.target.value || null }))}
        className="bg-surface border border-border text-t2 text-xs rounded px-2 py-1.5 focus:outline-none focus:border-border2"
      >
        <option value="">All events</option>
        <option value="planetary">Planetary</option>
        <option value="galactic">Galactic</option>
        <option value="showdown">Showdown</option>
      </select>
    </div>
  )
}
