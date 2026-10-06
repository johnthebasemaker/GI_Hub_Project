/**
 * Phase 21f — the self-driving demos (flows 1 and 2, ruling Q21-14: the
 * Finance story first). Each one is ALSO an E2E spec in the Practice leg
 * (tests/e2e/specs/demo.spec.ts), so a demo that would stall in front of
 * management fails a pull request first.
 *
 * Kept in step with `backend/api/practice_demo.CATALOG` (ids, titles, roles),
 * which the assistant reads to offer a demo — suite 21F checks they agree.
 *
 * ⚠️ Every entry a demo makes carries `DEMO-` (the remark, or the DEMO- tank),
 * so Reset demo data (HOD / Admin) removes exactly what demos made (Q21-17).
 * No supplier prices and no real site names in a sentence (Q20-17).
 */
import { tid } from './types'
import type { DemoScript } from './types'

const stamp = () => {
  const d = new Date()
  const p = (n: number) => String(n).padStart(2, '0')
  return `DEMO-${p(d.getDate())}${p(d.getMonth() + 1)}-${p(d.getHours())}${p(d.getMinutes())}${p(d.getSeconds())}`
}

export const DEMO_TANK = 'DEMO-TANK-1'

export const SCRIPTS: DemoScript[] = [
  {
    id: 'consumption-to-approval',
    title: 'Issue stock, then the HOD approves it',
    blurb: 'The store keeper records an issue with its paper; the HOD approves it into the ledger.',
    roles: ['store_keeper', 'hod', 'admin'],
    vars: () => ({ ref: stamp() }),
    beats: [
      { say: 'This demo runs on Practice data. Nothing here touches the live warehouse.' },
      { say: 'We start as the store keeper, at the warehouse counter.', do: 'switch', value: 'store_keeper' },
      { say: 'This is Issue Stock. Everything that leaves the store is written here first.',
        do: 'navigate', value: '/entry/issue', until: '#SAP_Code' },
      { say: 'First the material. The store keeper finds it by name, not by code.',
        do: 'select', target: '#SAP_Code', value: 'Masking Tape', as: 'item' },
      { say: 'The line under it shows what is in stock right now, and how many days that lasts.',
        do: 'point', target: tid('item-snapshot'), optional: true, timeout: 5000 },
      { say: 'Two of them left the store today.', do: 'type', target: '#Quantity', value: '2' },
      { say: 'The work package it is charged to.', do: 'select', target: '#wbs', value: 'WBS',
        optional: true, timeout: 3000 },
      { say: 'Who received it.', do: 'type', target: '#Issued_To', value: 'Crew A' },
      { say: 'The remark carries a DEMO tag, so a trainer can clear this entry afterwards.',
        do: 'type', target: '#Remarks', value: '{ref} practice demo' },
      { say: 'Add to batch. A store keeper can add many lines and send them together.',
        do: 'click', target: 'button', text: 'Add to batch', untilText: 'Batch (1 line)' },
      { say: 'Every entry carries its paper. The demo attaches a practice slip, as you would attach a photo of the note.',
        do: 'attach', target: tid('entry-docs'), value: '{ref}', untilText: '{ref}.png' },
      { say: 'Submit the batch to the HOD.', do: 'click', target: 'button', text: 'Submit batch to HOD',
        untilText: 'submitted for HOD approval' },
      { say: 'Nothing has left the ledger yet. It waits for the HOD.' },
      { say: 'Now we sign out, and sign in as the HOD.', do: 'switch', value: 'hod' },
      { say: 'The HOD opens Approvals: everything the site has submitted, by kind.',
        do: 'navigate', value: '/hod/approvals', until: '.ant-tabs-tab' },
      { say: 'Issues.', do: 'click', target: '.ant-tabs-tab', text: 'Issues' },
      { say: 'Here is the store keeper’s line, with its DEMO tag. The analysis under it compares the draw with this material’s usual use.',
        do: 'point', within: { css: '.ant-table-row', hasText: '{ref}' } },
      { say: 'The HOD approves it.', do: 'click', within: { css: '.ant-table-row', hasText: '{ref}' },
        target: 'button', text: 'Approve' },
      { say: 'And confirms. Approval commits it to the ledger: the stock figure changes now, and the audit log records who approved it.',
        do: 'click', target: '.ant-popconfirm .ant-btn-primary', untilText: 'Approved' },
      { say: 'That is the loop: written once at the counter, checked once by the HOD, and in the stock figure the moment it is approved.' },
    ],
  },
  {
    id: 'ss-bulk',
    title: 'Surface Shield jobs: bulk submit, then bulk approve',
    blurb: 'The supervisor files a week of lining jobs in one go; the HOD approves them in one go.',
    roles: ['store_keeper', 'supervisor', 'hod', 'admin'],
    vars: () => ({ tank: DEMO_TANK }),
    beats: [
      { say: 'This demo runs on Practice data, on a demo tank kept for it.' },
      { say: 'We start as the site supervisor, who files the lining work done each day.',
        do: 'switch', value: 'supervisor' },
      { say: 'Execution. Each card is one day of Surface Shield material drawn for one tank.',
        do: 'navigate', value: '/execution', until: 'input[aria-label="Find a job"]' },
      { say: 'We look at the demo tank.', do: 'type', target: 'input[aria-label="Find a job"]', value: '{tank}' },
      { say: 'A card is ready when the store keeper’s remark gives the area. The supervisor checks it; nothing is typed twice.',
        do: 'point', within: { css: '.gi-job-card', hasText: '{tank}' } },
      { say: 'Select all ready picks every complete card.', do: 'click', target: tid('bulk-select-ready') },
      { say: 'One review of the whole batch: the days, the tanks, the total area.',
        do: 'click', target: tid('bulk-submit'), until: tid('bulk-submit-confirm') },
      { say: 'And one click sends them all to the HOD.', do: 'click', target: tid('bulk-submit-confirm'),
        untilText: 'Submitted' },
      { say: 'Now we sign out, and sign in as the HOD.', do: 'switch', value: 'hod' },
      { say: 'The HOD opens the same page.', do: 'navigate', value: '/execution', until: '.ant-tabs-tab' },
      { say: 'Awaiting the HOD: every job submitted from the site.', do: 'click', target: '.ant-tabs-tab',
        text: 'Awaiting the HOD' },
      { say: 'The HOD ticks the demo tank’s jobs.', do: 'check',
        within: { css: '.gi-job-card', hasText: '{tank}' }, target: 'input[type=checkbox]' },
      { say: 'Approve selected. The confirmation shows the total area, and names any job drawn more than ten percent off the recipe before the click.',
        do: 'click', target: tid('hod-approve-selected'), until: tid('hod-approve-confirm') },
      { say: 'Approved. Each job’s area is credited to the tank once, and the material it drew is recorded against it.',
        do: 'click', target: tid('hod-approve-confirm'), untilText: 'Approved' },
      { say: 'A week of lining, filed and approved in two clicks each — with every number still checked.' },
    ],
  },
]

export const scriptById = (id: string) => SCRIPTS.find((s) => s.id === id)
