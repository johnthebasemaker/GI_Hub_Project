import { Link } from 'react-router-dom'

/** Phase 17 — the System One router's two non-prose answers. */
export interface NavTarget { path: string; label: string }
export interface DataTable {
  intent: string; message: string; columns: string[]; rows: unknown[][]
  total_rows: number; metric?: { label: string; value: unknown; entries?: unknown }
}

// ⚠️ LAZY ON PURPOSE. HubAssistant ships in the login critical path (AppLayout
// imports it), which may not grow (scripts/critical_path_check.mjs). These two
// renderers are fetched the first time a router answer needs one, so the cost
// on the critical path is the import stub, not the table.
export default function AssistantResult({ nav, table, onNavigate }: {
  nav: NavTarget | null; table: DataTable | null; onNavigate: () => void
}) {
  return (
    <>
      {/* UI_COMMAND (ruling Q17-7): navigation ONLY. The server resolved the
          page among the routes this role may open; the link goes through the
          same route guard as the sidebar, so it grants nothing. */}
      {nav && (
        <Link to={nav.path} onClick={onNavigate} style={{ display: 'block', marginBottom: 8 }}>
          <span style={{ display: 'block', padding: '5px 10px', borderRadius: 6, fontSize: 12.5,
                         border: '1px solid var(--gi-gold, #C9A227)', color: 'var(--gi-gold, #C9A227)' }}>
            Open {nav.label} →
          </span>
        </Link>
      )}
      {/* SQL_QUERY (ruling Q17-8): the template lane's rows, in the chat. The
          same bound-parameter query /ai/query runs, site-scoped from the JWT. */}
      {table && (
        <div style={{ marginBottom: 8, maxHeight: 220, overflow: 'auto',
                      border: '1px solid rgba(128,128,128,0.25)', borderRadius: 6 }}>
          {table.metric && (
            <div style={{ padding: '6px 8px', fontSize: 12.5 }}>
              {table.metric.label}: <b>{String(table.metric.value ?? 0)}</b>
            </div>
          )}
          {!table.metric && table.rows.length > 0 && (
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11.5 }}>
              <thead>
                <tr>{table.columns.map((c) => (
                  <th key={c} style={{ textAlign: 'left', padding: '4px 6px', position: 'sticky',
                                       top: 0, background: 'var(--gi-surface, #1f2937)' }}>{c}</th>
                ))}</tr>
              </thead>
              <tbody>
                {table.rows.map((r, i) => (
                  <tr key={i}>{r.map((v, j) => (
                    <td key={j} style={{ padding: '3px 6px', borderTop: '1px solid rgba(128,128,128,0.15)' }}>
                      {v === null || v === undefined ? '' : String(v)}
                    </td>
                  ))}</tr>
                ))}
              </tbody>
            </table>
          )}
          {/* An empty result must SAY it is empty — a bare caption reads as a
              table that failed to load. */}
          {!table.metric && table.rows.length === 0 && (
            <div style={{ padding: '6px 8px', fontSize: 12.5 }}>No matching records.</div>
          )}
          <div style={{ padding: '4px 8px', fontSize: 11, opacity: 0.7 }}>
            {table.message}
            {table.total_rows > table.rows.length
              ? ` · showing ${table.rows.length} of ${table.total_rows} — open Reports for the full list`
              : ''}
          </div>
        </div>
      )}
    </>
  )
}
