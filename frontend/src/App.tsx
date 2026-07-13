import { lazy } from 'react'
import { Route, Routes } from 'react-router-dom'
import AppLayout from './components/AppLayout'
import LoginPage from './pages/LoginPage'
import { useAuth } from './auth/AuthContext'

// Route pages are code-split — each becomes its own chunk, loaded on demand,
// so the initial bundle stays small. AppLayout renders a <Suspense> around the
// <Outlet>, so the sidebar stays put while a page chunk loads.
const Dashboard = lazy(() => import('./pages/Dashboard'))
const StockPage = lazy(() => import('./pages/StockPage'))
const RecordsPage = lazy(() => import('./pages/RecordsPage'))
const MasterDataPage = lazy(() => import('./pages/MasterDataPage'))
const ReceivePage = lazy(() => import('./pages/ReceivePage'))
const IssuePage = lazy(() => import('./pages/IssuePage'))
const ReturnPage = lazy(() => import('./pages/ReturnPage'))
const AdjustPage = lazy(() => import('./pages/AdjustPage'))
const StockCountPage = lazy(() => import('./pages/StockCountPage'))
const ReturnablesPage = lazy(() => import('./pages/ReturnablesPage'))
const ApprovalsPage = lazy(() => import('./pages/ApprovalsPage'))
const ExecutiveSummaryPage = lazy(() => import('./pages/ExecutiveSummaryPage'))
const BurnRatePage = lazy(() => import('./pages/BurnRatePage'))
const LowStockPage = lazy(() => import('./pages/LowStockPage'))
const HodPrsPage = lazy(() => import('./pages/HodPrsPage'))
const LiningCoveragePage = lazy(() => import('./pages/LiningCoveragePage'))
const DocumentLibraryPage = lazy(() => import('./pages/DocumentLibraryPage'))
const LogisticsPage = lazy(() => import('./pages/LogisticsPage'))
const WarehousePage = lazy(() => import('./pages/WarehousePage'))
const IncomingDeliveriesPage = lazy(() => import('./pages/IncomingDeliveriesPage'))
const SupervisorPage = lazy(() => import('./pages/SupervisorPage'))
const SkRequestsPage = lazy(() => import('./pages/SkRequestsPage'))
const SmePage = lazy(() => import('./pages/SmePage'))
const UsersPage = lazy(() => import('./pages/UsersPage'))
const PendingUsersPage = lazy(() => import('./pages/PendingUsersPage'))
const AuditLogPage = lazy(() => import('./pages/AuditLogPage'))
const InventoryAdminPage = lazy(() => import('./pages/InventoryAdminPage'))
const SecurityPage = lazy(() => import('./pages/SecurityPage'))
const ReportsPage = lazy(() => import('./pages/ReportsPage'))
const DocumentsPage = lazy(() => import('./pages/DocumentsPage'))
const AdminConsolePage = lazy(() => import('./pages/AdminConsolePage'))
const OverdueActionsPage = lazy(() => import('./pages/OverdueActionsPage'))
const CrossSitePage = lazy(() => import('./pages/CrossSitePage'))
const ManHoursPage = lazy(() => import('./pages/ManHoursPage'))
const OcrImportPage = lazy(() => import('./pages/OcrImportPage'))
const FeedbackPage = lazy(() => import('./pages/FeedbackPage'))

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
        <Route path="entry/count" element={<StockCountPage />} />
        <Route path="entry/returnables" element={<ReturnablesPage />} />
        <Route path="entry/ocr" element={<OcrImportPage />} />
        <Route path="site/incoming" element={<IncomingDeliveriesPage />} />
        <Route path="supervisor" element={<SupervisorPage />} />
        <Route path="sk/requests" element={<SkRequestsPage />} />
        <Route path="sme" element={<SmePage />} />
        <Route path="hod/approvals" element={<ApprovalsPage />} />
        <Route path="hod/executive-summary" element={<ExecutiveSummaryPage />} />
        <Route path="hod/burn-rate" element={<BurnRatePage />} />
        <Route path="hod/low-stock" element={<LowStockPage />} />
        <Route path="hod/prs" element={<HodPrsPage />} />
        <Route path="hod/lining-coverage" element={<LiningCoveragePage />} />
        <Route path="hod/documents" element={<DocumentLibraryPage />} />
        <Route path="logistics/lining-coverage" element={<LiningCoveragePage />} />
        <Route path="logistics" element={<LogisticsPage />} />
        <Route path="warehouse" element={<WarehousePage />} />
        <Route path="records/:key" element={<RecordsPage />} />
        <Route path="master/:key" element={<MasterDataPage />} />
        <Route path="admin/users" element={<UsersPage />} />
        <Route path="admin/pending" element={<PendingUsersPage />} />
        <Route path="admin/overdue" element={<OverdueActionsPage />} />
        <Route path="admin/inventory" element={<InventoryAdminPage />} />
        <Route path="admin/audit" element={<AuditLogPage />} />
        <Route path="reports" element={<ReportsPage />} />
        <Route path="documents" element={<DocumentsPage />} />
        <Route path="admin/console" element={<AdminConsolePage />} />
        <Route path="hod/requests" element={<CrossSitePage />} />
        <Route path="manhours" element={<ManHoursPage />} />
        <Route path="feedback" element={<FeedbackPage />} />
        <Route path="security" element={<SecurityPage />} />
      </Route>
    </Routes>
  )
}
