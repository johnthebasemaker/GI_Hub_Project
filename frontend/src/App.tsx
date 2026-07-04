import { Route, Routes } from 'react-router-dom'
import AppLayout from './components/AppLayout'
import Dashboard from './pages/Dashboard'
import StockPage from './pages/StockPage'
import RecordsPage from './pages/RecordsPage'
import MasterDataPage from './pages/MasterDataPage'
import ReceivePage from './pages/ReceivePage'
import IssuePage from './pages/IssuePage'
import ReturnPage from './pages/ReturnPage'
import AdjustPage from './pages/AdjustPage'
import LoginPage from './pages/LoginPage'
import ApprovalsPage from './pages/ApprovalsPage'
import BurnRatePage from './pages/BurnRatePage'
import HodPrsPage from './pages/HodPrsPage'
import LogisticsPage from './pages/LogisticsPage'
import WarehousePage from './pages/WarehousePage'
import IncomingDeliveriesPage from './pages/IncomingDeliveriesPage'
import SupervisorPage from './pages/SupervisorPage'
import SkRequestsPage from './pages/SkRequestsPage'
import SmePage from './pages/SmePage'
import UsersPage from './pages/UsersPage'
import AuditLogPage from './pages/AuditLogPage'
import InventoryAdminPage from './pages/InventoryAdminPage'
import SecurityPage from './pages/SecurityPage'
import { useAuth } from './auth/AuthContext'

export default function App() {
  const { user } = useAuth()
  if (!user) return <LoginPage />
  return (
    <Routes>
      <Route element={<AppLayout />}>
        <Route index element={<Dashboard />} />
        <Route path="stock" element={<StockPage />} />
        <Route path="entry/receive" element={<ReceivePage />} />
        <Route path="entry/issue" element={<IssuePage />} />
        <Route path="entry/return" element={<ReturnPage />} />
        <Route path="entry/adjust" element={<AdjustPage />} />
        <Route path="site/incoming" element={<IncomingDeliveriesPage />} />
        <Route path="supervisor" element={<SupervisorPage />} />
        <Route path="sk/requests" element={<SkRequestsPage />} />
        <Route path="sme" element={<SmePage />} />
        <Route path="hod/approvals" element={<ApprovalsPage />} />
        <Route path="hod/burn-rate" element={<BurnRatePage />} />
        <Route path="hod/prs" element={<HodPrsPage />} />
        <Route path="logistics" element={<LogisticsPage />} />
        <Route path="warehouse" element={<WarehousePage />} />
        <Route path="records/:key" element={<RecordsPage />} />
        <Route path="master/:key" element={<MasterDataPage />} />
        <Route path="admin/users" element={<UsersPage />} />
        <Route path="admin/inventory" element={<InventoryAdminPage />} />
        <Route path="admin/audit" element={<AuditLogPage />} />
        <Route path="security" element={<SecurityPage />} />
      </Route>
    </Routes>
  )
}
