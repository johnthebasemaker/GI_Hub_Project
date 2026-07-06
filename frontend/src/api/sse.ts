import { getAuthToken } from './client'

// Shared SSE consumer (Phase AI-5) — the HubAssistant pattern extracted:
// axios buffers whole responses and EventSource can't carry the bearer
// header, so streams use fetch + ReadableStream. Each `data: {...}` frame is
// parsed and handed to onEvent; malformed frames are skipped.
export async function streamSse(
  path: string,
  body: unknown,
  onEvent: (ev: Record<string, unknown>) => void,
  signal?: AbortSignal,
): Promise<void> {
  const res = await fetch(`/api${path}`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${getAuthToken() ?? ''}`,
    },
    body: JSON.stringify(body ?? {}),
    signal,
  })
  if (!res.ok || !res.body) throw new Error(`HTTP ${res.status}`)
  const reader = res.body.getReader()
  const dec = new TextDecoder()
  let buf = ''
  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    buf += dec.decode(value, { stream: true })
    const frames = buf.split('\n\n')
    buf = frames.pop() ?? ''
    for (const f of frames) {
      const line = f.split('\n').find((l) => l.startsWith('data: '))
      if (!line) continue
      try {
        onEvent(JSON.parse(line.slice(6)))
      } catch { /* skip malformed frame */ }
    }
  }
}
