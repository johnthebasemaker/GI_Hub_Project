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
const LocatorPage = lazy(() => import('./pages/LocatorPage'))
const AssetsPage = lazy(() => import('./pages/AssetsPage'))
const MaterialCardPage = lazy(() => import('./pages/MaterialCardPage'))
const RecordsPage = lazy(() => import('./pages/RecordsPage'))
const MasterDataPage = lazy(() => import('./pages/MasterDataPage'))
const ReceivePage = lazy(() => import('./pages/ReceivePage'))
const IssuePage = lazy(() => import('./pages/IssuePage'))
const ReturnPage = lazy(() => import('./pages/ReturnPage'))
const AdjustPage = lazy(() => import('./pages/AdjustPage'))
const StockCountPage = lazy(() => import('./pages/StockCountPage'))
const ReturnablesPage = lazy(() => import('./pages/ReturnablesPage'))
const BulkImportPage = lazy(() => import('./pages/BulkImportPage'))
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
const ExecutionPage = lazy(() => import('./pages/ExecutionPage'))
const SurfaceShieldLogPage = lazy(() => import('./pages/SurfaceShieldLogPage'))
const SkRequestsPage = lazy(() => import('./pages/SkRequestsPage'))
const QcInspectionsPage = lazy(() => import('./pages/QcInspectionsPage'))
const QcAccountsPage = lazy(() => import('./pages/QcAccountsPage'))
const PpeRulesPage = lazy(() => import('./pages/PpeRulesPage'))
const PpeForecastPage = lazy(() => import('./pages/PpeForecastPage'))
const EmployeesPage = lazy(() => import('./pages/EmployeesPage'))
const SmePage = lazy(() => import('./pages/SmePage'))
const UsersPage = lazy(() => import('./pages/UsersPage'))
const PendingUsersPage = lazy(() => import('./pages/PendingUsersPage'))
const AuditLogPage = lazy(() => import('./pages/AuditLogPage'))
const InventoryAdminPage = lazy(() => import('./pages/InventoryAdminPage'))
const LotRegisterPage = lazy(() => import('./pages/LotRegisterPage'))
const SecurityPage = lazy(() => import('./pages/SecurityPage'))
const ReportsPage = lazy(() => import('./pages/ReportsPage'))
const DocumentsPage = lazy(() => import('./pages/DocumentsPage'))
const AdminConsolePage = lazy(() => import('./pages/AdminConsolePage'))
const OverdueActionsPage = lazy(() => import('./pages/OverdueActionsPage'))
const CrossSitePage = lazy(() => import('./pages/CrossSitePage'))
const ManHoursPage = lazy(() => import('./pages/ManHoursPage'))
const QcHodPage = lazy(() => import('./pages/QcHodPage'))
const OcrImportPage = lazy(() => import('./pages/OcrImportPage'))
const AiTracesPage = lazy(() => import('./pages/AiTracesPage'))
const WbsPage = lazy(() => import('./pages/WbsPage'))
const FeedbackPage = lazy(() => import('./pages/FeedbackPage'))
const TrainingPage = lazy(() => import('./pages/TrainingPage'))
const RequestsPendingPage = lazy(() => import('./pages/RequestsPendingPage'))
const CataloguePage = lazy(() => import('./pages/CataloguePage'))

export default function App() {
  const { user } = useAuth()
  if (!user) return <LoginPage />
  return (
    <Routes>
      <Route element={<AppLayout />}>
        <Route index element={<Dashboard />} />
        <Route path="stock" element={<StockPage />} />
        <Route path="lots" element={<LotRegisterPage />} />
        <Route path="requests-pending" element={<RequestsPendingPage />} />
        <Route path="catalogue" element={<CataloguePage />} />
        <Route path="locator" element={<LocatorPage />} />
        <Route path="assets" element={<AssetsPage />} />
        {/* Where a QR scan lands. The param may be a SAP code, a Material_Code
            or a raw label payload — the server resolves all three. */}
        <Route path="stock/material/:sap" element={<MaterialCardPage />} />
        <Route path="entry/receive" element={<ReceivePage />} />
        <Route path="entry/issue" element={<IssuePage />} />
        <Route path="entry/return" element={<ReturnPage />} />
        <Route path="entry/adjust" element={<AdjustPage />} />
        <Route path="entry/count" element={<StockCountPage />} />
        <Route path="entry/returnables" element={<ReturnablesPage />} />
        <Route path="entry/ocr" element={<OcrImportPage />} />
        <Route path="admin/ai-traces" element={<AiTracesPage />} />
        <Route path="site/incoming" element={<IncomingDeliveriesPage />} />
        <Route path="supervisor" element={<SupervisorPage />} />
        <Route path="execution" element={<ExecutionPage />} />
        <Route path="surface-shield/log" element={<SurfaceShieldLogPage />} />
        <Route path="sk/requests" element={<SkRequestsPage />} />
        <Route path="qc/inspections" element={<QcInspectionsPage />} />
        <Route path="qc/accounts" element={<QcAccountsPage />} />
        <Route path="ppe/rules" element={<PpeRulesPage />} />
        <Route path="ppe/forecast" element={<PpeForecastPage />} />
        <Route path="hr/employees" element={<EmployeesPage />} />
        <Route path="sme" element={<SmePage />} />
        <Route path="bulk-import" element={<BulkImportPage />} />
        <Route path="hod/approvals" element={<ApprovalsPage />} />
        <Route path="hod/executive-summary" element={<ExecutiveSummaryPage />} />
        <Route path="hod/burn-rate" element={<BurnRatePage />} />
        <Route path="hod/low-stock" element={<LowStockPage />} />
        <Route path="hod/prs" element={<HodPrsPage />} />
        <Route path="hod/lining-coverage" element={<LiningCoveragePage />} />
        <Route path="hod/documents" element={<DocumentLibraryPage />} />
        <Route path="hod/wbs" element={<WbsPage />} />
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
        <Route path="qc-hod" element={<QcHodPage />} />
        <Route path="feedback" element={<FeedbackPage />} />
        <Route path="security" element={<SecurityPage />} />
        <Route path="training" element={<TrainingPage />} />
        {/* Any other path — `/login` after signing in, a bookmark, a typo —
            still renders the layout, whose guard sends an unlisted path to
            the role's home page. Without this there is no match at all and
            the screen is blank (canAccessPath refuses it, so nothing leaks). */}
        <Route path="*" element={null} />
      </Route>
    </Routes>
  )
}
