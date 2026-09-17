import { useEffect, useState } from 'react'
import { NavLink, Route, Routes, useLocation } from 'react-router-dom'
import Home from './pages/Home'
import Ask from './pages/Ask'
import ChainPage from './pages/Chain'
import Agents from './pages/Agents'
import UsagePage from './pages/Usage'
import Connectors from './pages/Connectors'
import Logs from './pages/Logs'
import MemoryPage from './pages/Memory'
import ExportPage from './pages/Export'
import SchedulesPage from './pages/Schedules'
import BriefPage from './pages/Brief'

/** The bundle is rebuilt often; when the server's UI version changes, offer a reload instead of
 *  letting the tab keep running stale code. */
function useNewVersion() {
  const [stale, setStale] = useState(false)
  useEffect(() => {
    let first: string | null = null
    const check = () => fetch('/api/health').then((r) => r.json()).then((h: { ui_version?: string }) => {
      if (!h.ui_version) return
      if (first === null) first = h.ui_version
      else if (h.ui_version !== first) setStale(true)
    }).catch(() => {})
    check(); const t = setInterval(check, 20000)
    return () => clearInterval(t)
  }, [])
  return stale
}

export default function App() {
  const stale = useNewVersion()
  const loc = useLocation()
  // the printable export stands alone: no sidebar, light theme
  if (/^\/ask\/[^/]+\/export$/.test(loc.pathname) || /^\/brief\/[^/]+$/.test(loc.pathname)) return <Routes><Route path="/ask/:cid/export" element={<ExportPage />} /><Route path="/brief/:id" element={<BriefPage />} /></Routes>
  return (
    <div className="layout">
      {stale && <div className="stale-banner">A newer version of Alfred's UI is available — <button className="btn-link" onClick={() => location.reload()}>reload</button></div>}
      <nav>
        <div className="brand">Alfred</div>
        <NavLink to="/" end>Home</NavLink>
        <NavLink to="/ask">Ask</NavLink>
        <NavLink to="/agents">Agents</NavLink>
        <NavLink to="/memory">Memory</NavLink>
        <NavLink to="/schedules">Schedules</NavLink>
        <NavLink to="/usage">Usage</NavLink>
        <NavLink to="/connectors">Connectors</NavLink>
        <NavLink to="/logs">Logs</NavLink>
      </nav>
      <main>
        <Routes>
          <Route path="/" element={<Home />} />
          <Route path="/ask" element={<Ask />} />
          <Route path="/ask/:cid" element={<Ask />} />
          <Route path="/chains/:id" element={<ChainPage />} />
          <Route path="/agents" element={<Agents />} />
          <Route path="/memory" element={<MemoryPage />} />
          <Route path="/schedules" element={<SchedulesPage />} />
          <Route path="/usage" element={<UsagePage />} />
          <Route path="/connectors" element={<Connectors />} />
          <Route path="/logs" element={<Logs />} />
        </Routes>
      </main>
    </div>
  )
}
