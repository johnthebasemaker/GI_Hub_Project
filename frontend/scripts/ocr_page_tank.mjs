#!/usr/bin/env node
/**
 * frontend/scripts/ocr_page_tank.mjs — the OCR page's row rules (Phase 23b).
 *
 * Ruling Q23-2: the page tank fills ONLY ditto / blank / unknown rows and never
 * overrides an explicit input. Ruling Q23-3: rows the second read finds go in
 * at their printed S.No without disturbing the rows already on the page.
 *
 * Run:  npm run test:ui-math   (from frontend/; it runs this after the SME math)
 */
import { fillPageTank, inheritTankFromAbove, insertBySno, pageTankFillable, undoPageTank }
  from '../src/lib/pageTank.ts'

let passed = 0
const failures = []
function check(name, cond, detail = '') {
  if (cond) { passed += 1; console.log(`  ✅ ${name}`) } else { failures.push(name); console.log(`  ❌ ${name}  — ${detail}`) }
}

const page = [
  { tank_no: '84Do-Tnk-001', tank_tag: null, tank_state: 'unknown' },          // garbled first cell
  { tank_no: '″', tank_tag: null, tank_state: 'unknown', tank_group: '84Do-Tnk-001' }, // ditto
  { tank_no: '', tank_tag: null, tank_state: 'blank' },                        // blank
  { tank_no: 'J027', tank_tag: 'J027', tank_state: 'auto', tank_source: 'exact' }, // written + matched
  { tank_no: 'K-TNK-91', tank_tag: null, tank_state: 'suggested' },            // a gold "check"
  { tank_no: '″', tank_tag: 'J050', tank_state: 'auto', tank_source: 'accepted' }, // set by hand
]
const r = fillPageTank(page, '522-89D0-TNK-001')
check('fills the garbled first cell, its ditto, the blank and the gold "check"',
  JSON.stringify(r.filled) === '[0,1,2,4]', `filled=${JSON.stringify(r.filled)}`)
check('never touches a tank written AND matched on its row', r.rows[3].tank_tag === 'J027')
check('never touches the store keeper\'s own choice', r.rows[5].tank_tag === 'J050'
  && r.rows[5].tank_source === 'accepted')
check('filled rows say where their tank came from (page) and count as matched',
  r.rows[0].tank_source === 'page' && r.rows[0].tank_state === 'auto')
check('reports how many rows it kept', r.kept === 2, `kept=${r.kept}`)
const again = fillPageTank(r.rows, 'J091')
check('a second page-tank choice re-fills the rows the first one filled, and only those',
  JSON.stringify(again.filled) === '[0,1,2,4]' && again.rows[3].tank_tag === 'J027')
const back = undoPageTank(r.rows, r.undo)
const keyed = page.map((x, i) => ({ ...x, _key: `r${i}` }))
const rk = fillPageTank(keyed, 'J091')
const shifted = insertBySno(rk.rows.map((x, i) => ({ ...x, sno: String(i * 2 + 1) })),
  [{ sno: '2', _key: 's2', tank_no: '', tank_tag: 'J091', tank_source: 'page' }])
const undone = undoPageTank(shifted, rk.undo)
check('undo still finds the right rows after the second read inserts one (keyed, not positional)',
  undone[0].tank_tag === null && undone[1]._key === 's2' && undone[1].tank_tag === 'J091'
  && undone[4].tank_tag === 'J027', JSON.stringify(undone.map((x) => [x._key, x.tank_tag])))
check('undo puts back exactly what was there',
  back.every((x, i) => x.tank_tag === page[i].tank_tag && x.tank_state === page[i].tank_state))
const edited = r.rows.map((x, i) => (i === 1 ? { ...x, tank_tag: 'J050', tank_source: 'accepted' } : x))
check('undo leaves a row the store keeper changed after the fill',
  undoPageTank(edited, r.undo)[1].tank_tag === 'J050')
check('a row with a written, unmatched tank is fillable (it is unknown)',
  pageTankFillable({ tank_no: 'S4D0', tank_tag: null, tank_state: 'unknown' }))

// the second read: rows 2–4 and 9 slot in at their S.No
const read = [{ sno: '1', k: 'a' }, { sno: '7', k: 'b' }, { sno: '8', k: 'c' }, { sno: '13', k: 'd' }]
const added = [{ sno: '9', k: 'n9' }, { sno: '2', k: 'n2' }, { sno: '4', k: 'n4' }, { sno: '3', k: 'n3' }]
const merged = insertBySno(read, added)
check('second-read rows go in at their printed S.No',
  merged.map((x) => x.sno).join(',') === '1,2,3,4,7,8,9,13', merged.map((x) => x.sno).join(','))
check('a row already on the page is never duplicated',
  insertBySno(read, [{ sno: '7', k: 'dup' }]).length === read.length)
check('the rows already there keep their objects (keys, edits)',
  merged[0] === read[0] && merged[4] === read[1])
const inh = inheritTankFromAbove([
  { tank_no: 'J027', tank_tag: 'J027', tank_state: 'auto', tank_source: 'accepted' },
  { tank_no: '″', tank_tag: null, tank_state: 'ditto' },
  { tank_no: 'J050', tank_tag: null, tank_state: 'unknown' }], [1, 2])
check('an added ditto row takes the tank above — but not as the store keeper\'s choice',
  inh[1].tank_tag === 'J027' && inh[1].tank_source === null)
check('an added row with its own written tank keeps it', inh[2].tank_no === 'J050' && inh[2].tank_tag === null)

console.log(`\n== OCR PAGE RULES: ${failures.length ? '❌ FAIL' : '✅ PASS'} `
  + `(${passed} passed, ${failures.length} failed) ==`)
if (failures.length) process.exit(1)
