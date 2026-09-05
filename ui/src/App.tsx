import { NavLink, Route, Routes } from 'react-router-dom'
import Dashboard from './pages/Dashboard'
import Ask from './pages/Ask'
import ChainPage from './pages/Chain'
import Agents from './pages/Agents'
import UsagePage from './pages/Usage'
import Connectors from './pages/Connectors'
import Logs from './pages/Logs'

export default function App() {
  return (
    <div className="layout">
      <nav>
        <div className="brand">Alfred</div>
        <NavLink to="/" end>Dashboard</NavLink>
        <NavLink to="/ask">Ask</NavLink>
        <NavLink to="/agents">Agents</NavLink>
        <NavLink to="/usage">Usage</NavLink>
        <NavLink to="/connectors">Connectors</NavLink>
        <NavLink to="/logs">Logs</NavLink>
      </nav>
      <main>
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/ask" element={<Ask />} />
          <Route path="/chains/:id" element={<ChainPage />} />
          <Route path="/agents" element={<Agents />} />
          <Route path="/usage" element={<UsagePage />} />
          <Route path="/connectors" element={<Connectors />} />
          <Route path="/logs" element={<Logs />} />
        </Routes>
      </main>
    </div>
  )
}
