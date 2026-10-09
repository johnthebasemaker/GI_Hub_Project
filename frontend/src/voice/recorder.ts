/**
 * Phase 23e — record the microphone and hand back a 16 kHz mono 16-bit WAV,
 * which is what Whistle reads (rulings Q23-10..12).
 *
 * The browser records in its own format (Opus/WebM on Chrome, AAC on Safari);
 * the WAV is made HERE, with the Web Audio API (decode → resample to 16 kHz
 * mono in an OfflineAudioContext → 16-bit PCM), so the server needs no ffmpeg
 * and never receives anything but plain samples. Nothing is kept: the stream
 * is stopped and the buffers dropped as soon as the WAV exists.
 */
export const MAX_SECONDS = 30
const RATE = 16000

export function micSupported(): boolean {
  return typeof navigator !== 'undefined' && !!navigator.mediaDevices?.getUserMedia
    && typeof window !== 'undefined' && 'MediaRecorder' in window
}

function encodeWav(samples: Float32Array, rate = RATE): Blob {
  const buf = new ArrayBuffer(44 + samples.length * 2)
  const v = new DataView(buf)
  const str = (o: number, s: string) => { for (let i = 0; i < s.length; i += 1) v.setUint8(o + i, s.charCodeAt(i)) }
  str(0, 'RIFF'); v.setUint32(4, 36 + samples.length * 2, true); str(8, 'WAVE')
  str(12, 'fmt '); v.setUint32(16, 16, true); v.setUint16(20, 1, true); v.setUint16(22, 1, true)
  v.setUint32(24, rate, true); v.setUint32(28, rate * 2, true); v.setUint16(32, 2, true); v.setUint16(34, 16, true)
  str(36, 'data'); v.setUint32(40, samples.length * 2, true)
  for (let i = 0; i < samples.length; i += 1) {
    const s = Math.max(-1, Math.min(1, samples[i]))
    v.setInt16(44 + i * 2, s < 0 ? s * 0x8000 : s * 0x7fff, true)
  }
  return new Blob([buf], { type: 'audio/wav' })
}

export async function toWav16k(recording: Blob): Promise<Blob> {
  const Ctx = window.AudioContext || (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext
  const ctx = new Ctx()
  try {
    const decoded = await ctx.decodeAudioData(await recording.arrayBuffer())
    const length = Math.max(1, Math.ceil(decoded.duration * RATE))
    const off = new OfflineAudioContext(1, length, RATE)
    const src = off.createBufferSource()
    src.buffer = decoded
    src.connect(off.destination)
    src.start()
    const rendered = await off.startRendering()
    return encodeWav(rendered.getChannelData(0).slice(0, Math.min(length, MAX_SECONDS * RATE)))
  } finally {
    void ctx.close()
  }
}

/** One recording: start(), then stop() → WAV. `level` reports 0–1 while recording. */
export class Recording {
  private rec: MediaRecorder | null = null
  private stream: MediaStream | null = null
  private chunks: Blob[] = []
  private meter: { ctx: AudioContext; raf: number } | null = null

  async start(level?: (v: number) => void): Promise<void> {
    this.stream = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true } })
    this.chunks = []
    this.rec = new MediaRecorder(this.stream)
    this.rec.ondataavailable = (e) => { if (e.data.size) this.chunks.push(e.data) }
    this.rec.start()
    if (level) {
      const ctx = new AudioContext()
      const an = ctx.createAnalyser()
      an.fftSize = 256
      ctx.createMediaStreamSource(this.stream).connect(an)
      const data = new Uint8Array(an.frequencyBinCount)
      const tick = () => {
        an.getByteTimeDomainData(data)
        let peak = 0
        for (const x of data) peak = Math.max(peak, Math.abs(x - 128))
        level(Math.min(1, peak / 64))
        if (this.meter) this.meter.raf = requestAnimationFrame(tick)
      }
      this.meter = { ctx, raf: requestAnimationFrame(tick) }
    }
  }

  async stop(): Promise<Blob> {
    const rec = this.rec
    if (!rec) throw new Error('not recording')
    const done = new Promise<void>((resolve) => { rec.onstop = () => resolve() })
    if (rec.state !== 'inactive') rec.stop()
    await done
    this.release()
    return toWav16k(new Blob(this.chunks, { type: rec.mimeType || 'audio/webm' }))
  }

  release(): void {
    if (this.meter) { cancelAnimationFrame(this.meter.raf); void this.meter.ctx.close(); this.meter = null }
    this.stream?.getTracks().forEach((t) => t.stop())
    this.stream = null
    this.rec = null
  }
}
