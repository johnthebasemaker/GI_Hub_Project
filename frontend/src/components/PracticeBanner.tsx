import { useEffect } from 'react'
import { Alert, Tag } from 'antd'
import { ExperimentOutlined, WarningOutlined } from '@ant-design/icons'
import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client'
import { CURRENT_ENV, ENV_LABEL } from '../api/environment'

/** What the SERVER says it is (GET /instance, unauthenticated). */
export interface InstanceInfo {
  instance: 'production' | 'training'
  label: string
  practice: boolean
  dataset_version?: string
  seeded_at?: string
  reset_at?: string
  reset_by?: string
}

/**
 * ⚠️ THE ONLY SOURCE FOR THE BANNER (rule 17, vector V10). The login toggle
 * says what the user ASKED for; this says what they GOT. A proxy that routed
 * `/training-api` to Live — or the reverse — shows up here as a mismatch
 * rather than behind a label that agrees with the toggle.
 */
export function useInstance() {
  return useQuery({
    queryKey: ['instance', CURRENT_ENV],
    queryFn: async () => (await api.get<InstanceInfo>('/instance')).data,
    staleTime: 60_000,
    retry: 1,
  })
}

function when(iso?: string): string {
  if (!iso) return ''
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString()
}

/**
 * The permanent Practice bar, and the red one for a mismatch. Not dismissible:
 * the whole point is that nobody forgets which environment they are in.
 * Also paints `PRACTICE ·` into the tab title and puts `gi-practice` on
 * <body>, which the stylesheet uses for the amber header accent and the print
 * watermark.
 */
export default function PracticeBanner({ compact = false }: { compact?: boolean }) {
  const { data } = useInstance()
  const serverPractice = data?.practice === true
  const mismatch = data != null && data.instance !== CURRENT_ENV

  useEffect(() => {
    const on = serverPractice || (data == null && CURRENT_ENV === 'training')
    document.body.classList.toggle('gi-practice', on)
    const base = document.title.replace(/^PRACTICE · /, '')
    document.title = on ? `PRACTICE · ${base}` : base
  }, [serverPractice, data])

  if (mismatch) {
    return (
      <Alert type="error" banner showIcon icon={<WarningOutlined />} className="gi-env-banner"
        title={`Environment mismatch — you chose ${ENV_LABEL[CURRENT_ENV]}, but this server is ${ENV_LABEL[data!.instance]}.`}
        description={compact ? undefined
          : 'Every change will be refused until this is fixed. Sign out and pick the environment again, or tell your administrator.'} />
    )
  }
  if (!serverPractice) return null
  return (
    <Alert type="warning" banner showIcon icon={<ExperimentOutlined />}
      className="gi-env-banner gi-practice-banner"
      title={<strong>PRACTICE — practice data, nothing here is real.</strong>}
      description={compact ? undefined : (
        <>
          Nothing you do here reaches Live, and no WhatsApp or email is ever sent.
          Photo reading (OCR) is off, and training videos watched here are not counted.
          {data?.reset_at ? ` Last reset ${when(data.reset_at)}${data.reset_by ? ` by ${data.reset_by}` : ''}.` : ''}
        </>
      )} />
  )
}

/** The header chip — stays visible after the banner scrolls away. Server-driven
 * like the banner. Pulses (Phase 15c) like the badge beside the logo; on a
 * phone, where the sider is a drawer, it IS the top-left indicator. */
export function PracticeTag() {
  const { data } = useInstance()
  if (!data?.practice) return null
  return (
    <Tag color="orange" icon={<ExperimentOutlined />} className="gi-practice-tag gi-practice-pulse"
      style={{ marginInlineEnd: 0, fontWeight: 600 }}>
      PRACTICE
    </Tag>
  )
}

/**
 * Phase 15c — the pulsing amber PRACTICE badge at the top left of every page,
 * under the GI Hub wordmark (and top-left on the login once Practice is
 * chosen, `floating`). Server-driven like everything else here (V10): a page
 * that is not talking to a Practice server never shows it. The pulse is a
 * glow, about once a second, and stands still under reduced motion.
 */
export function PracticeBadge({ floating = false }: { floating?: boolean }) {
  const { data } = useInstance()
  if (!data?.practice) return null
  return (
    <span role="status" aria-label="Practice mode — nothing here is real"
      className={`gi-practice-badge gi-practice-pulse${floating ? ' gi-practice-badge--float' : ''}`}>
      <ExperimentOutlined /> PRACTICE
    </span>
  )
}
