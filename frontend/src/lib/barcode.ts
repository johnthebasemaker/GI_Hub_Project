// Barcode/QR material picking (UAT Phase 3 QoL). Decoding happens client-side
// in QrScanner (BarcodeDetector when available, jsQR fallback for QR); this
// module maps a decoded string onto an inventory SAP code.

import type { Row } from '../api/client'

// 1-D retail/logistics formats + QR — what BarcodeDetector supports broadly.
export const BARCODE_FORMATS = [
  'qr_code', 'code_128', 'code_39', 'code_93', 'ean_13', 'ean_8',
  'upc_a', 'upc_e', 'itf', 'codabar', 'data_matrix',
]

/** Decoded text → SAP code, or null when nothing matches.
 *  Tries: exact SAP · case-insensitive SAP · a "SAP:<code>" style payload ·
 *  a code embedded anywhere in the scan (labels often wrap the code). */
export function matchScanToSap(decoded: string, items: Row[]): string | null {
  const text = (decoded || '').trim()
  if (!text) return null
  const saps = items.map((r) => String(r.SAP_Code ?? '').trim()).filter(Boolean)
  const exact = saps.find((s) => s === text)
  if (exact) return exact
  const ci = saps.find((s) => s.toLowerCase() === text.toLowerCase())
  if (ci) return ci
  const m = text.match(/(?:sap|mat(?:erial)?)[:=\s]+([A-Za-z0-9_-]+)/i)
  if (m) {
    const tagged = saps.find((s) => s.toLowerCase() === m[1].toLowerCase())
    if (tagged) return tagged
  }
  // Longest SAP contained in the scanned text (avoid matching '1' in '1001').
  const contained = saps
    .filter((s) => s.length >= 4 && text.includes(s))
    .sort((a, b) => b.length - a.length)
  return contained[0] ?? null
}
