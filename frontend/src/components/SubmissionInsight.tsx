/**
 * frontend/src/components/SubmissionInsight.tsx — T1 Submission Intelligence.
 *
 * Reviewer summary for one pending submission, fetched lazily from
 * GET /ai/submission-summary. The server computes deterministic ledger stats
 * and (when the flag + local Ollama allow) phrases them with llama3.1:8b —
 * numbers always come from the ledger, never the model. Review screens never
 * block on this: it renders a tiny skeleton and fails silent-soft.
 */
import { Alert, Skeleton, Tag } from 'antd'
import { RobotOutlined } from '@ant-design/icons'
import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client'

export interface SubmissionSummary {
  kind: string
  ref_id: number
  summary: string
  tone: 'success' | 'info' | 'warning' | 'error'
  source: 'ai' | 'deterministic'
}

export function useSubmissionSummary(kind: string, refId: number | null) {
  return useQuery({
    queryKey: ['/ai/submission-summary', kind, refId],
    enabled: refId !== null,
    staleTime: 5 * 60_000,
    retry: false,
    queryFn: async () =>
      (await api.get<SubmissionSummary>('/ai/submission-summary',
        { params: { kind, ref_id: refId } })).data,
  })
}

export default function SubmissionInsight({ kind, refId }: {
  kind: 'staged-issue' | 'xsite'
  refId: number
}) {
  const { data, isLoading, isError } = useSubmissionSummary(kind, refId)
  if (isLoading) return <Skeleton.Input active size="small" style={{ width: 360 }} />
  if (isError || !data) return null // insight is advisory — never block review
  return (
    <Alert
      type={data.tone === 'error' ? 'error' : data.tone}
      showIcon
      style={{ padding: '6px 10px' }}
      message={(
        <span style={{ fontSize: '0.82rem' }}>
          {data.summary}{' '}
          <Tag style={{ marginLeft: 6 }} color={data.source === 'ai' ? 'geekblue' : 'default'}>
            {data.source === 'ai' ? <><RobotOutlined /> AI</> : 'stats'}
          </Tag>
        </span>
      )}
    />
  )
}
