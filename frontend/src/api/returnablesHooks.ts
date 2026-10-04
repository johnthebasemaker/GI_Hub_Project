// Return-desk hooks (Phase 18 Track 3).
//
// ⚠️ A SEPARATE MODULE ON PURPOSE. `api/hooks.ts` is imported by the login page
// and therefore sits on the critical path, which may not grow by a byte
// (Phase 14e, scripts/critical_path_check.mjs). Only ReturnablesPage — a lazy
// route — imports this file, so none of it reaches the sign-in screen.
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from './client'
import type { Row } from './client'

export type ReturnCondition = 'ok' | 'damaged' | 'incomplete'

function invalidate(qc: ReturnType<typeof useQueryClient>) {
  qc.invalidateQueries({ queryKey: ['/entry/returnables'] })
  qc.invalidateQueries({ queryKey: ['/meta/work-queues'] })
}

/** One loan back, optionally with its condition (no body = "in good order"). */
export function useReturnOne() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, ...body }: { id: number; condition?: ReturnCondition; note?: string }) =>
      api.post(`/entry/returnables/${id}/return`, body).then((r) => r.data),
    onSuccess: () => invalidate(qc),
  })
}

/** A borrower's whole kit in one press. Loans that cannot be returned come
 *  back in `skipped` with their reason — the rest still close. */
export function useReturnBatch() {
  const qc = useQueryClient()
  return useMutation({
    // `qtys` (Phase 19c): {loan id: how many came back} for loans returned
    // only in part; any loan not named returns everything still out.
    mutationFn: (body: { ids: number[]; condition: ReturnCondition; note?: string;
      qtys?: Record<number, number> }) =>
      api.post<{ returned: number[]; partial: { id: number; qty: number; left: number }[];
        skipped: { id: number; status: number; reason: string }[] }>(
        '/entry/returnables/return-batch', body).then((r) => r.data),
    onSuccess: () => invalidate(qc),
  })
}

export interface ReturnScan {
  code: string
  kind: 'loan' | 'item' | 'employee' | 'material' | 'none'
  loans: Row[]
  employee: { id_number: string; name: string; phone?: string; department?: string; active?: boolean } | null
  material: { SAP_Code: string; description?: string | null; uom?: string | null; item_ref: string } | null
  message: string
}

/** One scanned/typed code → the open loans it names. */
export async function resolveReturnScan(code: string): Promise<ReturnScan> {
  return (await api.get<ReturnScan>('/entry/returnables/resolve', { params: { code } })).data
}

/** The borrower's loan slip (ruling Q12): a small PDF whose QR is `#<id>`,
 *  opened in a new tab to print. The tab is opened BEFORE the fetch — after an
 *  `await` the click no longer counts as a user gesture and pop-up blockers
 *  eat it — and a blocked tab falls back to a download. */
export async function printLoanSlip(id: number | string): Promise<void> {
  const tab = window.open('', '_blank')
  let res
  try {
    res = await api.get(`/entry/returnables/${id}/slip`, { responseType: 'blob' })
  } catch (e) {
    tab?.close()
    throw e
  }
  const url = URL.createObjectURL(res.data as Blob)
  if (tab) {
    tab.location.href = url
  } else {
    const a = document.createElement('a')
    a.href = url
    a.download = `loan-slip-${id}.pdf`
    document.body.appendChild(a)
    a.click()
    a.remove()
  }
  // the tab has loaded the blob long before a minute is up
  window.setTimeout(() => URL.revokeObjectURL(url), 60_000)
}
