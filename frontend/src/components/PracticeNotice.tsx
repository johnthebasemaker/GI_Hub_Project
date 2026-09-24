import { Alert } from 'antd'
import { ExperimentOutlined } from '@ant-design/icons'
import { useInstance } from './PracticeBanner'

/**
 * The page-level explanation for each thing Practice deliberately does NOT do
 * (operator rulings Q2 and Q5). Server-driven like the banner, and only an
 * explanation: the server refuses the action itself (backend/api/practice.py),
 * so this is a courtesy, never the control.
 */
const TEXT = {
  ocr: {
    title: 'Photo reading (OCR) is switched off in Practice.',
    body: 'The AI engine is shared with Live and reads one page in minutes, so trainees never queue ahead of real forms. Use the paste lane or type the rows in — everything after the read works exactly as in Live.',
  },
  training: {
    title: 'Videos watched in Practice are not counted.',
    body: 'Watch as much as you like, but certificates are recorded in Live only. Sign in to Live to have a video counted towards your training.',
  },
} as const

export default function PracticeNotice({ kind }: { kind: keyof typeof TEXT }) {
  const { data } = useInstance()
  if (!data?.practice) return null
  const t = TEXT[kind]
  return (
    <Alert type="info" showIcon icon={<ExperimentOutlined />} style={{ marginBottom: 16 }}
      title={t.title} description={t.body} />
  )
}
