/**
 * global-setup — builds the ENTIRE isolated stack before any spec runs:
 *
 *   1. (re)create the throwaway Postgres DB `gihub_e2e_pw` on the local :5433
 *      cluster and load it with the REAL legacy data via the production
 *      cutover script (tools/migration/cutover_migrate.py --wipe).
 *   2. overwrite the role users' bcrypt hashes with a known E2E password —
 *      inside the throwaway DB only.
 *   3. spawn a hermetic uvicorn on :8010 (GI_DOTENV=0 ⇒ WhatsApp/SMTP disabled,
 *      GI_SCHEDULER=0 ⇒ no digest loop) pointed at the throwaway DB.
 *   4. spawn a Vite dev server on :5183 whose /api proxy targets :8010
 *      (VITE_API_PROXY, see frontend/vite.config.ts).
 *
 * PIDs are persisted to .runtime/pids.json; global-teardown kills both process
 * groups and DROPs the database. Nothing here touches gi_database.db, the
 * `gihub` mirror, or a developer's own :8000/:5173 servers.
 */
import { execFileSync, spawn } from 'node:child_process'
import * as fs from 'node:fs'
import * as path from 'node:path'
import {
  API_PORT, API_URL, ASYNC_DB_URL, AUTH_DIR, E2E_DB, E2E_PASSWORD, JWT_SECRET,
  PG_HOST, PG_PORT, PG_USER, PRACTICE_ADMIN_PASSWORD, PRACTICE_API_PORT,
  PRACTICE_API_URL, PRACTICE_DB, PRACTICE_DB_URL, PRACTICE_JWT_SECRET, PY, ROOT, RUNTIME_DIR,
  SYNC_DB_URL, USERS, WEB_PORT, WEB_URL,
} from './harness/env'

function psql(sql: string, db = 'postgres'): string {
  return execFileSync(
    'psql', ['-h', PG_HOST, '-p', PG_PORT, '-U', PG_USER, '-d', db, '-tAc', sql],
    { encoding: 'utf-8' },
  ).trim()
}

async function waitFor(url: string, label: string, timeoutMs = 90_000): Promise<void> {
  const t0 = Date.now()
  while (Date.now() - t0 < timeoutMs) {
    try {
      const r = await fetch(url)
      if (r.ok) return
    } catch { /* not up yet */ }
    await new Promise((r) => setTimeout(r, 500))
  }
  throw new Error(`${label} did not become ready at ${url} within ${timeoutMs / 1000}s`)
}

export default async function globalSetup() {
  fs.mkdirSync(RUNTIME_DIR, { recursive: true })
  fs.mkdirSync(AUTH_DIR, { recursive: true })

  // ── 1. throwaway DB, loaded with the real legacy data ────────────────────
  console.log(`[e2e] creating ${E2E_DB} and loading it via cutover_migrate.py …`)
  if (psql(`SELECT 1 FROM pg_database WHERE datname='${E2E_DB}'`) !== '1') {
    psql(`CREATE DATABASE ${E2E_DB}`)
  }
  execFileSync(
    PY, [path.join(ROOT, 'tools', 'migration', 'cutover_migrate.py'),
      '--wipe', '--target', SYNC_DB_URL],
    { cwd: ROOT, stdio: ['ignore', 'ignore', 'inherit'] },
  )

  // ── 1b. relax the entry-document gate for the functional specs ──────────
  // (require_entry_documents defaults ON in production; the entry-docs spec
  // flips it on itself to test the gate.)
  psql("INSERT INTO app_settings (key, value) VALUES ('require_entry_documents','0') "
       + "ON CONFLICT (key) DO UPDATE SET value='0'", E2E_DB)

  // ── 1b-ii. Phase 14a: one Surface Shield SAP with a Unit Size ────────────
  // The legacy SQLite predates the column, so a cut-over database has none.
  // BC 3004 is a 9 kg can in the real Inventory sheet; units.spec.ts reads it.
  psql('UPDATE inventory SET "Unit_Size" = 9, "Base_UOM" = \'KG\' '
       + 'WHERE "SAP_Code" = \'1045\'', E2E_DB)

  // ── 1b-iii. Phase 14c: one Surface Shield JOB (sme-jobs.spec.ts) ─────────
  // Two cans of a two-component system drawn on one day against one vessel,
  // with the area typed in Remarks — so the job card has a suggested system
  // code (9102 lists both) and a pre-filled area (12.5) to assert on.
  psql(
    "INSERT INTO inventory (\"SAP_Code\", \"Material_Code\", \"Equipment_Description\", "
    + "\"Category\", \"UOM\", \"Unit_Size\", \"Base_UOM\") "
    + "VALUES ('E2EJOB-1','E2EJOB-A','E2E job base','Surface Shields','Can',4,'KG'), "
    + "('E2EJOB-2','E2EJOB-B','E2E job hardener','Surface Shields','Can',1,'KG') "
    + "ON CONFLICT (\"SAP_Code\") DO NOTHING", E2E_DB)
  psql(
    "INSERT INTO sme_recipe (\"Lining_System_Code\", \"Lining_System_Name\", "
    + "\"Material_Code\", \"SAP_Code\", \"Material_Name\", \"UOM\", \"For_1_SQM\") "
    + "VALUES ('9102','E2E JOB','E2EJOB-A','E2EJOB-1','E2E job base','KG',0.8), "
    + "('9102','E2E JOB','E2EJOB-B','E2EJOB-2','E2E job hardener','KG',0.2)", E2E_DB)
  psql(
    "INSERT INTO sme_equipment (\"Site_ID\", \"Equipment_Tag_No\", \"Name\", "
    + "\"Lining_System_Code\", \"Surface_Area_SQM\") "
    + "VALUES ('CNCEC','E2E-JOB-TANK','E2E job tank','9102',200)", E2E_DB)
  psql(
    "INSERT INTO consumption (\"Date\", \"SAP_Code\", \"Quantity\", \"Site_ID\", "
    + "\"Tank_No\", \"Remarks\") VALUES "
    + "('2026-09-20','E2EJOB-1',2.5,'CNCEC','E2E-JOB-TANK','Shell - 12.5 SQM Done'), "
    + "('2026-09-20','E2EJOB-2',2.5,'CNCEC','E2E-JOB-TANK',NULL)", E2E_DB)

  // ── 1b-iv. Phase 15d: one GARNET job (sme-jobs.spec.ts) ───────────────────
  // Three TON (Unit Size 1000 → 3000 KG) of Garnet on a steel TANK, with the
  // area in Remarks. The recipe's ESC2 line gives NEW its workbook figure (20
  // KG/m²); the prep-code rows come from the migration's data step.
  psql(
    "INSERT INTO inventory (\"SAP_Code\", \"Material_Code\", \"Equipment_Description\", "
    + "\"Category\", \"UOM\", \"Unit_Size\", \"Base_UOM\") "
    + "VALUES ('E2EGAR-1','E2EGAR-A','E2E AUSTRALIAN GARNET 30/60','Surface Shields','TON',1000,'KG') "
    + "ON CONFLICT (\"SAP_Code\") DO NOTHING", E2E_DB)
  psql(
    "INSERT INTO sme_recipe (\"Lining_System_Code\", \"Execution_Sub_Activity_Code\", "
    + "\"Lining_System_Name\", \"Material_Code\", \"SAP_Code\", \"Material_Name\", \"UOM\", "
    + "\"For_1_SQM\") VALUES ('ESC2','ESC2','Blasting Steel Surface','E2EGAR-A','E2EGAR-1',"
    + "'E2E garnet','KG',20)", E2E_DB)
  psql(
    "INSERT INTO sme_equipment (\"Site_ID\", \"Equipment_Tag_No\", \"Name\", \"Type\", "
    + "\"Substrate\", \"Lining_System_Code\", \"Surface_Area_SQM\") "
    + "VALUES ('CNCEC','E2E-GAR-TANK','E2E garnet tank','ME','TANK','9102',300)", E2E_DB)
  psql(
    "INSERT INTO consumption (\"Date\", \"SAP_Code\", \"Quantity\", \"Site_ID\", "
    + "\"Tank_No\", \"Remarks\") VALUES "
    + "('2026-09-21','E2EGAR-1',3,'CNCEC','E2E-GAR-TANK','Shell - 140 SQM Done')", E2E_DB)

  // ── 1c. the SME tier-segregation fixture (sme-tiers.spec.ts) ─────────────
  // A purpose-built copy of the PHENACIN ACP POWDER shape that produced the
  // 2026-08-03 bug report: a material with ZERO stock on the shelf and MORE
  // than enough on an open purchase order. The portal used to render this as
  // a green "100% Fully Ready" pill. One tag, one system code, two materials:
  //   E2ETIER-ACP  0 available / 5000 on order   ← the blocker
  //   E2ETIER-OK   500 available / 0 on order
  // 100 m² remaining at 1.0 per m² each, so "ready now" is exactly 0% and
  // "with ordered" is exactly 100% — unambiguous to assert on.
  psql(
    "INSERT INTO sme_inventory_seed (\"Material_Code\", \"SAP_Code\", "
    + "\"Material_Name\", \"UOM\", \"Initial_Available_Qty\", \"Initial_Ordered_Qty\") "
    + "VALUES ('E2ETIER-ACP','E2E-1','E2E ACP Powder','KG',0,5000), "
    + "('E2ETIER-OK','E2E-2','E2E Stocked Material','KG',500,0) "
    + "ON CONFLICT (\"Material_Code\", \"SAP_Code\") DO UPDATE "
    + "SET \"Initial_Available_Qty\" = excluded.\"Initial_Available_Qty\", "
    + "\"Initial_Ordered_Qty\" = excluded.\"Initial_Ordered_Qty\"", E2E_DB)
  psql(
    "INSERT INTO sme_recipe (\"Lining_System_Code\", \"Lining_System_Name\", "
    + "\"Material_Code\", \"SAP_Code\", \"Material_Name\", \"UOM\", \"For_1_SQM\") "
    + "VALUES ('9101','E2E TIER','E2ETIER-ACP','E2E-1','E2E ACP Powder','KG',1.0), "
    + "('9101','E2E TIER','E2ETIER-OK','E2E-2','E2E Stocked Material','KG',1.0)", E2E_DB)
  psql(
    "INSERT INTO sme_equipment (\"Site_ID\", \"Equipment_Tag_No\", \"Name\", "
    + "\"Location\", \"Lining_System_Code\", \"Surface_Area_SQM\") "
    + "VALUES ('CNCEC','E2E-TIER-TANK','E2E tier probe tank','TRAIN J','9101',100)", E2E_DB)

  // ── 1d. the QSEP fixtures: QC accounts + a controlled material ───────────
  // The legacy data predates the `qc` role, so there is nobody to log in as.
  // Three accounts, because QC is the only DUAL-SCOPE role in the system and
  // each axis is a different branch of auth.qc_scope():
  //   e2e_qc       site-bound      → sees CNCEC inspections, no warehouse ones
  //   e2e_qc_wh    warehouse-bound → sees WH-01 inspections, no site ones
  //   e2e_qc_none  NEITHER         → must see NOTHING. This is the fail-closed
  //                                  account, and the reason it exists is that
  //                                  `if scope:` would hand it every site.
  // Passwords are set by step 2 along with everybody else's.
  psql(
    "INSERT INTO users (username, password_hash, role, \"Site_ID\", \"Warehouse_ID\") "
    + "VALUES ('e2e_qc','x','qc','CNCEC',NULL), "
    + "('e2e_qc_wh','x','qc',NULL,'WH-01'), "
    + "('e2e_qc_none','x','qc',NULL,NULL) "
    + "ON CONFLICT (username) DO UPDATE SET role = excluded.role, "
    + "\"Site_ID\" = excluded.\"Site_ID\", \"Warehouse_ID\" = excluded.\"Warehouse_ID\"",
    E2E_DB)

  // A Surface Shield with a known SAP so the QC specs never have to guess
  // which of the 36 controlled materials is safe to experiment on, and a
  // pending inspection against it so the queue is non-empty. Category is what
  // makes a material controlled — see services/quality.controlled_category.
  psql(
    "INSERT INTO inventory (\"SAP_Code\", \"Material_Code\", "
    + "\"Equipment_Description\", \"Category\", \"UOM\") "
    + "VALUES ('E2EQC-1','E2EQC-MAT','E2E controlled shield','Surface Shields','EA') "
    + "ON CONFLICT (\"SAP_Code\") DO UPDATE SET \"Category\" = excluded.\"Category\"",
    E2E_DB)
  psql(
    "INSERT INTO qc_inspections (\"Site_ID\", \"SAP_Code\", \"Material_Code\", "
    + "source_type, source_ref, submitted_qty, approved_qty, status, created_by) "
    + "VALUES ('CNCEC','E2EQC-1','E2EQC-MAT','receipt','E2E-SEED',100,0,'pending','e2e-setup')",
    E2E_DB)
  // Stock of it on the shelf at CNCEC, so the issue path has something to
  // refuse. Without a receipt the block would be untestable — the form would
  // fail on stock long before it reached the quality gate.
  psql(
    "INSERT INTO receipts (\"Date\", \"SAP_Code\", \"Quantity\", \"Site_ID\", \"Received_by\") "
    + "VALUES (CURRENT_DATE::text, 'E2EQC-1', 100, 'CNCEC', 'e2e-setup')", E2E_DB)
  // A Material Test Certificate for it, filed at CNCEC.
  //
  // ⚠️ Needed so the QC specs can isolate the INSPECTION half of the issue
  // gate. Since 2026-08-12 an issue is refused for two independent reasons —
  // no certificate (paperwork, Logistics' problem) and no QC approval
  // (inspection, the QC's problem). Without this row every quality assertion
  // below would pass on the wrong refusal, and the QC gate could rot away
  // completely without a single test noticing. `E2EQC-2` deliberately has NO
  // certificate — that is the material the MTC spec drives.
  psql(
    "INSERT INTO mtc_documents (\"Site_ID\", \"SAP_Code\", \"Material_Code_Ref\", "
    + "mtc_number, submitted_by, status) "
    + "VALUES ('CNCEC','E2EQC-1','E2EQC-MAT','E2E-MTC-1','e2e-setup','attached')",
    E2E_DB)

  // A SECOND controlled material with stock, an approved inspection, and
  // deliberately NO certificate anywhere: the only thing standing between it
  // and the field is the MTC gate, which is what `qsep-mtc.spec.ts` proves.
  psql(
    "INSERT INTO inventory (\"SAP_Code\", \"Material_Code\", "
    + "\"Equipment_Description\", \"Category\", \"UOM\") "
    + "VALUES ('E2EQC-2','E2EQC-MAT-2','E2E uncertified shield','Surface Shields','EA') "
    + "ON CONFLICT (\"SAP_Code\") DO UPDATE SET \"Category\" = excluded.\"Category\"",
    E2E_DB)
  psql(
    "INSERT INTO qc_inspections (\"Site_ID\", \"SAP_Code\", \"Material_Code\", "
    + "source_type, source_ref, submitted_qty, approved_qty, status, created_by) "
    + "VALUES ('CNCEC','E2EQC-2','E2EQC-MAT-2','receipt','E2E-SEED-2',100,100,"
    + "'approved','e2e-setup')",
    E2E_DB)
  psql(
    "INSERT INTO receipts (\"Date\", \"SAP_Code\", \"Quantity\", \"Site_ID\", \"Received_by\") "
    + "VALUES (CURRENT_DATE::text, 'E2EQC-2', 100, 'CNCEC', 'e2e-setup')", E2E_DB)

  // ── 1e. the PPE fixtures ─────────────────────────────────────────────────
  // A PPE-category material, a usable-time rule, an employee to receive it,
  // and stock to issue. All four are needed before the issue form will even
  // reach the PPE validation: no rule means no expiry (so no early-replacement
  // case to test), and no employee means the first guard fires and the later
  // ones are never exercised.
  psql(
    "INSERT INTO inventory (\"SAP_Code\", \"Material_Code\", "
    + "\"Equipment_Description\", \"Category\", \"UOM\") "
    + "VALUES ('E2EPPE-1','E2EPPE-MAT','E2E safety boots','PPE','PR') "
    + "ON CONFLICT (\"SAP_Code\") DO UPDATE SET \"Category\" = excluded.\"Category\"",
    E2E_DB)
  psql(
    "INSERT INTO ppe_rules (\"SAP_Code\", \"Site_ID\", usable_days, "
    + "requires_safety_doc, created_by) "
    + "VALUES ('E2EPPE-1', NULL, 90, 0, 'e2e-setup')", E2E_DB)
  psql(
    "INSERT INTO employees (\"ID_Number\", \"Name\", \"Site_ID\", status) "
    + "VALUES ('E2E-EMP-1','E2E Worker One','CNCEC','active') "
    + "ON CONFLICT (\"ID_Number\") DO UPDATE SET \"Site_ID\" = excluded.\"Site_ID\", "
    + "status = excluded.status", E2E_DB)
  psql(
    "INSERT INTO employees (\"ID_Number\", \"Name\", \"Site_ID\", status) "
    + "VALUES ('E2E-EMP-2','E2E Worker Two','SVCQ-OTHER','active') "
    + "ON CONFLICT (\"ID_Number\") DO UPDATE SET \"Site_ID\" = excluded.\"Site_ID\"",
    E2E_DB)
  psql(
    "INSERT INTO receipts (\"Date\", \"SAP_Code\", \"Quantity\", \"Site_ID\", \"Received_by\") "
    + "VALUES (CURRENT_DATE::text, 'E2EPPE-1', 50, 'CNCEC', 'e2e-setup')", E2E_DB)

  // ── 1f. the roles the legacy data has nobody for ─────────────────────────
  // The legacy data has no warehouse_user, auditor or Head of Qualities to log
  // in as, so the matrix spec could otherwise only assert six of the nine roles
  // — and those are the ones the RBAC passes changed most. A warehouse user is
  // warehouse-bound with NO site; an auditor is global and view-only; a Head of
  // Qualities is global, view-mostly, and scoped to one material category.
  psql(
    "INSERT INTO users (username, password_hash, role, \"Site_ID\", \"Warehouse_ID\") "
    + "VALUES ('e2e_wh','x','warehouse_user',NULL,'WH-01'), "
    + "('e2e_auditor','x','auditor',NULL,NULL), "
    + "('e2e_qc_hod','x','qc_hod',NULL,NULL) "
    + "ON CONFLICT (username) DO UPDATE SET role = excluded.role, "
    + "\"Site_ID\" = excluded.\"Site_ID\", \"Warehouse_ID\" = excluded.\"Warehouse_ID\"",
    E2E_DB)

  // ── 1g. Phase 14d: a What's-new audience of ONE (announcements.spec.ts) ──
  // The panel opens by itself for its audience and the specs run in parallel,
  // so the fixture announcement is addressed to a role at a site no other spec
  // signs in at. Inserted as a DRAFT with its audience already derived (qc can
  // open /stock); the spec publishes it through the admin API.
  psql(
    "INSERT INTO users (username, password_hash, role, \"Site_ID\", \"Warehouse_ID\") "
    + "VALUES ('e2e_news','x','qc','E2E-NEWS',NULL) ON CONFLICT (username) DO NOTHING",
    E2E_DB)
  psql(
    "INSERT INTO feature_announcements (key, title, body, routes, audience_roles, sites, "
    + "manual_section, status) VALUES ('e2e-news','E2E: glass announcement', "
    + "'A fixture announcement for one QC at one site.', '[\"/stock\"]', '[\"qc\"]', "
    + "'[\"E2E-NEWS\"]', '4.11', 'draft') ON CONFLICT (key) DO NOTHING", E2E_DB)

  // ── 1h. stock vs the Excel workbook (stock-excel.spec.ts) ─────────────────
  // One stored check naming SAP 1001, so the Stock page's banner, its red
  // "≠ Excel" tag and the Material Card's alert have something to show.
  psql(
    "INSERT INTO stock_excel_checks (\"Site_ID\", workbook, checked_by, total_saps, matched, items) "
    + "VALUES ('CNCEC', 'CNCEC_Inventory.xlsx', 'e2e-setup', 505, 504, "
    + "'[{\"sap\": \"1001\", \"description\": \"E2E fixture material\", \"uom\": \"Can\", \"excel_row\": 47, \"workbook\": 21, \"app\": 25.75, \"difference\": 4.75, \"unexplained\": 0, \"causes\": [{\"code\": \"app_only\", \"effect\": 4.75, \"what\": \"A receipt of 4.75 on 2026-06-11 was entered in GI Hub by e2e, and the Receipt Log does not have it.\", \"fix\": \"If it really happened, add it to the Receipt Log.\", \"where\": {\"ledger\": \"receipts\", \"id\": 1}}]}]')", E2E_DB)

  // ── 2. known passwords for the role users (throwaway DB only) ────────────
  const resetScript = [
    'import bcrypt, sys',
    'from sqlalchemy import create_engine, text',
    `e = create_engine(${JSON.stringify(SYNC_DB_URL)})`,
    `h = bcrypt.hashpw(${JSON.stringify(E2E_PASSWORD)}.encode(), bcrypt.gensalt(rounds=4)).decode()`,
    'with e.begin() as c:',
    `    n = c.execute(text("UPDATE users SET password_hash=:h WHERE username = ANY(:u)"),`,
    `                  {"h": h, "u": ${JSON.stringify(Object.values(USERS))}}).rowcount`,
    'print(f"[e2e] reset {n} user password(s)")',
    `assert n == ${Object.values(USERS).length}, f"expected ${Object.values(USERS).length} users, matched {n}"`,
  ].join('\n')
  execFileSync(PY, ['-c', resetScript], { cwd: ROOT, stdio: 'inherit' })

  // ── 2b. the Practice sandbox (rule 17) ───────────────────────────────────
  // Built by the SHIPPING tool, not a harness copy of it (the P12-4 argument:
  // a second builder is a second place for isolation to be got wrong). `wall`
  // revokes CONNECT on this run's Live database from the Practice role — the
  // Live API below connects as a superuser and is unaffected.
  const practiceEnv = {
    ...process.env,
    LIVE_DB: E2E_DB,
    PRACTICE_DATABASE_URL: PRACTICE_DB_URL,
    PRACTICE_ADMIN_URL: `postgresql://${PG_USER}@${PG_HOST}:${PG_PORT}/postgres`,
    PRACTICE_ADMIN_PASSWORD,
    GI_DOTENV: '0',
  }
  console.log('[e2e] building the Practice sandbox via tools/practice_db.py …')
  for (const step of ['wall', 'build']) {
    execFileSync(PY, [path.join(ROOT, 'tools', 'practice_db.py'), step],
      { cwd: ROOT, env: practiceEnv, stdio: ['ignore', 'ignore', 'inherit'] })
  }
  // Same relaxation as step 1b, for the same reason: the practice spec's
  // offline receipt carries no supporting document.
  psql("INSERT INTO app_settings (key, value) VALUES ('require_entry_documents','0') "
       + "ON CONFLICT (key) DO UPDATE SET value='0'", PRACTICE_DB)

  // ── 3. hermetic backend ───────────────────────────────────────────────────
  console.log(`[e2e] starting uvicorn on :${API_PORT} …`)
  const apiLog = fs.openSync(path.join(RUNTIME_DIR, 'api.log'), 'w')
  const api = spawn(
    PY, ['-m', 'uvicorn', 'backend.api.main:app', '--host', '127.0.0.1', '--port', String(API_PORT)],
    {
      cwd: ROOT,
      detached: true,
      stdio: ['ignore', apiLog, apiLog],
      env: {
        ...process.env,
        GI_DOTENV: '0',
        GI_SCHEDULER: '0',
        JWT_SECRET,
        DATABASE_URL: ASYNC_DB_URL,
      },
    },
  )
  api.unref()

  // ── 3b. the Practice API — a second PROCESS, as in production ───────────
  console.log(`[e2e] starting the Practice uvicorn on :${PRACTICE_API_PORT} …`)
  const practiceLog = fs.openSync(path.join(RUNTIME_DIR, 'practice.log'), 'w')
  const {
    WHATSAPP_TOKEN: _w1, WHATSAPP_PHONE_NUMBER_ID: _w2, SMTP_HOST: _s1, SMTP_SERVER: _s2,
    SMTP_USER: _s3, SMTP_PASS: _s4, GI_AI_VISION_API_KEY: _v, GI_AI_RO_URL: _ro,
    ...cleanEnv
  } = process.env
  void [_w1, _w2, _s1, _s2, _s3, _s4, _v, _ro]
  const practice = spawn(
    PY, ['-m', 'uvicorn', 'backend.api.main:app', '--host', '127.0.0.1',
      '--port', String(PRACTICE_API_PORT)],
    {
      cwd: ROOT,
      detached: true,
      stdio: ['ignore', practiceLog, practiceLog],
      env: {
        ...cleanEnv,
        GI_INSTANCE: 'training',
        GI_DOTENV: '0',
        GI_SCHEDULER: '0',
        JWT_SECRET: PRACTICE_JWT_SECRET,
        DATABASE_URL: PRACTICE_DB_URL.replace('postgresql://', 'postgresql+asyncpg://'),
      },
    },
  )
  practice.unref()

  // ── 4. Vite dev server proxying /api → the hermetic backend ──────────────
  console.log(`[e2e] starting Vite on :${WEB_PORT} …`)
  const webLog = fs.openSync(path.join(RUNTIME_DIR, 'web.log'), 'w')
  const web = spawn(
    'npm', ['run', 'dev', '--', '--host', '127.0.0.1', '--port', String(WEB_PORT), '--strictPort'],
    {
      cwd: path.join(ROOT, 'frontend'),
      detached: true,
      stdio: ['ignore', webLog, webLog],
      env: {
        ...process.env, VITE_API_PROXY: API_URL, VITE_PRACTICE_PROXY: PRACTICE_API_URL,
        BROWSER: 'none',
      },
    },
  )
  web.unref()

  fs.writeFileSync(
    path.join(RUNTIME_DIR, 'pids.json'),
    JSON.stringify({ api: api.pid, practice: practice.pid, web: web.pid }, null, 2),
  )

  await waitFor(`${API_URL}/health`, 'backend')
  await waitFor(`${PRACTICE_API_URL}/health`, 'Practice backend')
  await waitFor(WEB_URL, 'frontend')
  await warmVite()
  console.log('[e2e] stack ready — backend :%d, frontend :%d, db %s', API_PORT, WEB_PORT, E2E_DB)
}

/**
 * Make Vite transform the app ONCE, before any worker starts.
 *
 * ⚠️ This is not an optimisation, it is a determinism fix, and it was worth
 * chasing because the symptom looked like a flaky test. `waitFor(WEB_URL)`
 * proves the dev server answers — it does NOT prove the dev server has
 * transformed anything. On a cold `node_modules/.vite` (any run after a
 * frontend edit, and every CI run) the first request for each module triggers
 * an on-demand transform, and with four workers all requesting different
 * modules at once the whole suite slows by roughly 5x: 30s warm, 2.8m cold.
 *
 * Nothing in the suite noticed except `sme-tiers`, whose `beforeAll` renders
 * the heaviest page in the product and blew a 60s budget — so the failure
 * presented as "the SME grid is flaky" when the SME grid was fine and the
 * bundler was busy. Raising the timeout would have hidden it; the cost is
 * paid once here instead, off the clock of every spec.
 *
 * Best-effort: a failure here must never fail the run. The worst case is the
 * old behaviour.
 */
async function warmVite(): Promise<void> {
  const t0 = Date.now()
  try {
    const html = await (await fetch(WEB_URL)).text()
    // EVERY script tag, not the first. Dev HTML leads with `/@vite/client`,
    // which is a few KB and transforms nothing — warming only that looked
    // like it was working (0.3s, no error) while the app entry stayed cold.
    // The conventional entry is added as a fallback so a silent regex miss
    // cannot turn this into a no-op that still logs a reassuring line.
    const srcs = [...html.matchAll(/<script[^>]+src="([^"]+)"/g)].map((m) => m[1])
    if (!srcs.some((s) => s.includes('main'))) srcs.push('/src/main.tsx')
    let bytes = 0
    for (const s of srcs) {
      const r = await fetch(new URL(s, WEB_URL).href)
      if (r.ok) bytes += (await r.text()).length
    }
    console.log(`[e2e] vite warmed ${srcs.length} entr${srcs.length === 1 ? 'y' : 'ies'} `
      + `(${(bytes / 1024).toFixed(0)} KB) in ${((Date.now() - t0) / 1000).toFixed(1)}s`)
    return
  } catch { /* best-effort — see the docstring */ }
  console.log(`[e2e] vite warm-up skipped after ${((Date.now() - t0) / 1000).toFixed(1)}s`)
}
