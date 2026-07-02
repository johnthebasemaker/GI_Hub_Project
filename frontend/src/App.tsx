import { Route, Routes } from 'react-router-dom'
import AppLayout from './components/AppLayout'
import Dashboard from './pages/Dashboard'
import StockPage from './pages/StockPage'
import RecordsPage from './pages/RecordsPage'
import MasterDataPage from './pages/MasterDataPage'
import ReceivePage from './pages/ReceivePage'

export default function App() {
  return (
    <Routes>
      <Route element={<AppLayout />}>
        <Route index element={<Dashboard />} />
        <Route path="stock" element={<StockPage />} />
        <Route path="entry/receive" element={<ReceivePage />} />
        <Route path="records/:key" element={<RecordsPage />} />
        <Route path="master/:key" element={<MasterDataPage />} />
      </Route>
    </Routes>
  )
}
