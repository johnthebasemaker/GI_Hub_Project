import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client'

export interface SttStatus { available: boolean; provider: string; reason: string | null; max_seconds: number }

/** Is voice input set up on this server? (the microphone hides itself if not) */
export function useSttStatus() {
  return useQuery<SttStatus>({
    queryKey: ['/ai/stt/status'],
    staleTime: 10 * 60_000,
    retry: false,
    queryFn: async () => (await api.get('/ai/stt/status')).data,
  })
}

export async function transcribe(wav: Blob): Promise<string> {
  const fd = new FormData()
  fd.append('audio', wav, 'speech.wav')
  const r = await api.post<{ text: string }>('/ai/stt', fd)
  return r.data.text
}
