import MicButton from './MicButton'
import ReadAloud from './ReadAloud'
import VoiceBar from './VoiceBar'

/**
 * Phase 23e — ONE lazy entry for everything voice, so the always-loaded part
 * of the app carries one import instead of three (the sign-in page's critical
 * path may not grow).
 */
export type VoiceProps =
  | { kind: 'bar' }
  | { kind: 'mic'; onText: (t: string) => void; testId?: string }
  | { kind: 'read'; text: string; testId?: string }

export default function Voice(p: VoiceProps) {
  if (p.kind === 'bar') return <VoiceBar />
  if (p.kind === 'mic') return <MicButton onText={p.onText} testId={p.testId} />
  return <ReadAloud text={p.text} testId={p.testId} />
}
