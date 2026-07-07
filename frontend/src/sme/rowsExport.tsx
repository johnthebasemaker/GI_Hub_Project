/**
 * frontend/src/sme/rowsExport.tsx — legacy-parity export of CLIENT-computed
 * rows (dashboard filters, variance views). The legacy portal exported the
 * displayed frame verbatim; POST /sme/export/rows renders the same rows
 * server-side into xlsx / pdf so the file format matches the old build.
 */
import { useState } from 'react'
import { App, Button, Space } from 'antd'
import { FileExcelOutlined, FilePdfOutlined } from '@ant-design/icons'
import { postDownloadDocument } from '../api/hooks'

type Cell = string | number | boolean | null
export type RowsDoc = { title: string; filenameStem: string; columns: string[]; rows: Cell[][] }

export async function exportRowsDoc(doc: RowsDoc, format: 'xlsx' | 'pdf') {
  await postDownloadDocument('/sme/export/rows', {
    title: doc.title, columns: doc.columns, rows: doc.rows, format,
    filename: doc.filenameStem,
  }, `${doc.filenameStem}.${format}`)
}

export function RowsExportButtons({ doc, size = 'small' }: {
  doc: () => RowsDoc
  size?: 'small' | 'middle'
}) {
  const { message } = App.useApp()
  const [busy, setBusy] = useState<string | null>(null)
  const dl = async (format: 'xlsx' | 'pdf') => {
    setBusy(format)
    try {
      await exportRowsDoc(doc(), format)
    } catch {
      message.error('Export failed')
    } finally {
      setBusy(null)
    }
  }
  return (
    <Space size={4}>
      <Button size={size} icon={<FileExcelOutlined />} loading={busy === 'xlsx'}
        onClick={() => dl('xlsx')}>Excel</Button>
      <Button size={size} icon={<FilePdfOutlined />} loading={busy === 'pdf'}
        onClick={() => dl('pdf')}>PDF</Button>
    </Space>
  )
}
