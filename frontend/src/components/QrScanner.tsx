import { useCallback, useEffect, useRef, useState } from 'react'
import { Alert, Button, Input, Modal, Space, Typography } from 'antd'
import jsQR from 'jsqr'

// Live QR badge scanner — Phase AI-4 Smart Scan tier 1.
// Decoding is 100% CLIENT-SIDE: frames go video → canvas → BarcodeDetector
// (native, when available) or jsQR (wasm-free fallback). The video stream
// NEVER leaves the browser; only the decoded ID string hits the server
// (GET /ai/badge/{id}). A manual-entry field covers damaged badges and
// camera-less desktops — same decode path from the caller's perspective.
interface Props {
  open: boolean
  title?: string
  onClose: () => void
  onDecode: (text: string) => void
}

export default function QrScanner({ open, title = 'Scan badge', onClose, onDecode }: Props) {
  const videoRef = useRef<HTMLVideoElement>(null)
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const streamRef = useRef<MediaStream | null>(null)
  const rafRef = useRef<number>(0)
  const doneRef = useRef(false)
  const [cameraError, setCameraError] = useState<string | null>(null)
  const [manual, setManual] = useState('')

  const stop = useCallback(() => {
    cancelAnimationFrame(rafRef.current)
    streamRef.current?.getTracks().forEach((t) => t.stop())
    streamRef.current = null
  }, [])

  const finish = useCallback((text: string) => {
    if (doneRef.current) return
    doneRef.current = true
    stop()
    onDecode(text.trim())
  }, [onDecode, stop])

  useEffect(() => {
    if (!open) { stop(); return }
    doneRef.current = false
    setCameraError(null)
    setManual('')

    let detector: { detect: (src: CanvasImageSource) => Promise<{ rawValue: string }[]> } | null = null
    const BD = (window as unknown as { BarcodeDetector?: new (o: { formats: string[] }) => typeof detector }).BarcodeDetector
    if (BD) {
      try { detector = new BD({ formats: ['qr_code'] }) } catch { detector = null }
    }

    const scan = async () => {
      const video = videoRef.current
      const canvas = canvasRef.current
      if (!video || !canvas || doneRef.current) return
      if (video.readyState >= 2 && video.videoWidth > 0) {
        if (detector) {
          try {
            const codes = await detector.detect(video)
            if (codes.length && codes[0].rawValue) { finish(codes[0].rawValue); return }
          } catch { detector = null /* fall back to jsQR */ }
        } else {
          // jsQR path: downscale to ≤480px for cheap per-frame decode.
          const scale = Math.min(1, 480 / video.videoWidth)
          canvas.width = Math.round(video.videoWidth * scale)
          canvas.height = Math.round(video.videoHeight * scale)
          const ctx = canvas.getContext('2d', { willReadFrequently: true })
          if (ctx) {
            ctx.drawImage(video, 0, 0, canvas.width, canvas.height)
            const img = ctx.getImageData(0, 0, canvas.width, canvas.height)
            const code = jsQR(img.data, img.width, img.height, { inversionAttempts: 'dontInvert' })
            if (code?.data) { finish(code.data); return }
          }
        }
      }
      rafRef.current = requestAnimationFrame(scan)
    }

    navigator.mediaDevices?.getUserMedia({ video: { facingMode: 'environment' } })
      .then((stream) => {
        streamRef.current = stream
        if (videoRef.current) {
          videoRef.current.srcObject = stream
          videoRef.current.play().catch(() => undefined)
        }
        rafRef.current = requestAnimationFrame(scan)
      })
      .catch((e: Error) => {
        setCameraError(
          e.name === 'NotAllowedError'
            ? 'Camera permission denied — allow camera access or type the badge ID below.'
            : 'No camera available — type the badge ID below.')
      })
    return stop
  }, [open, finish, stop])

  return (
    <Modal open={open} title={title} footer={null}
      onCancel={() => { stop(); onClose() }} destroyOnHidden>
      {cameraError ? (
        <Alert type="info" showIcon title={cameraError} style={{ marginBottom: 12 }} />
      ) : (
        <>
          <video ref={videoRef} muted playsInline
            style={{ width: '100%', borderRadius: 8, background: '#000' }} />
          <canvas ref={canvasRef} style={{ display: 'none' }} />
          <Typography.Paragraph type="secondary" style={{ marginTop: 8 }}>
            Hold the badge QR in front of the camera — it scans continuously.
            The video never leaves this device.
          </Typography.Paragraph>
        </>
      )}
      <Space.Compact style={{ width: '100%' }}>
        <Input placeholder="…or type the badge ID" value={manual}
          onChange={(e) => setManual(e.target.value)}
          onPressEnter={() => manual.trim() && finish(manual)} />
        <Button type="primary" disabled={!manual.trim()} onClick={() => finish(manual)}>
          Use ID
        </Button>
      </Space.Compact>
    </Modal>
  )
}
