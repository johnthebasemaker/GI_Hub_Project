"""training_modules for the three Phase 12 tutorials that had no row

Revision ID: d4f61b8e35ca
Revises: c3e58a7d24bf
Create Date: 2026-09-10 12:00:00

Phase 13c. The Hub Assistant's "Watch it" deep link works exactly as designed
and lands on nothing, for a reason nobody had looked at: **three of the four
recorded tutorials have no `training_modules` row at all.**

Slice 10b seeded exactly one module, `ocr_workflow_v1`, because it was the only
one the OCR gate needed. Phase 12 then recorded four tutorials, each declaring a
`training_module_key`. The matcher builds its URL from that key, `/training`
honours it, and the module list — correctly — contains no card to match. The
page renders, the URL is obeyed, and nothing at all scrolls into view.

    tools/tutorials/sk_stage_return_v1.yaml       → sk_stage_return_v1
    tools/tutorials/store_keeper_hub_assistant.yaml → hub_assistant_v1
    tools/tutorials/hod_executive_summary_v1.yaml → hod_executive_summary_v1

⚠️ NOTE THE SECOND ONE. Its `tutorial_id` is `store_keeper_hub_assistant` and
its `training_module_key` is `hub_assistant_v1` — they are deliberately
different, and `tutorials.TutorialBeat.url` prefers the module key. Seeding the
tutorial id instead would produce a link that resolves to nothing and looks
exactly like a UI bug.

⚠️ `required_roles` COMES FROM EACH SCRIPT'S `audience:`, NOT FROM ITS `role:`.
`role`/`hub_role` is who the screencast was RECORDED as; `audience` is who may
watch it, and P12-6 already makes it the deep-link fence. Two lists that mean
different things must not be conflated here — the OCR walk-through is recorded
as a supervisor and is `[supervisor, store_keeper]`, because a store keeper
verifies the quantities it explains.

⚠️ NO `training_assets` ROW IS SEEDED, and that stays true. Slice 10b's own
migration says why: a row pointing at a URI that does not exist yet renders a
broken player, and "not published yet" is the truthful state until the renders
land. `tools/generate_tutorial.py --publish` is what writes those rows, from a
manifest it has just produced.

⚠️ AND `mandatory` IS NOT SET BY THIS. `training.my_modules` computes it from
`required_roles`, so naming a role here does put the module in that role's list
— which is the point — but nothing about the SOFT gate changes: `gates_feature`
is left NULL on all three, so no interstitial appears anywhere. Only
`ocr_workflow_v1` gates a feature, exactly as before.

────────────────────────────────────────────────────────────────────────────
THIS MIGRATION WRITES ROWS, SO IT DECLARES `data_upgrade(conn)` AND CALLS IT
FROM `upgrade()` — rule 15's second half.

`tools/migration/cutover_migrate.py` builds production with
`metadata.create_all` and then replays every migration's data step. A migration
whose DML sits only inside `upgrade()` is SKIPPED on that path, producing a
production box with a correct schema over uncorrected data — silently.
`verify_data_migration_contract()` refuses such a migration in pre-flight.

Idempotent: `module_key` is unique and every insert is `ON CONFLICT DO NOTHING`,
so the cutover replay cannot duplicate a row.
"""
from alembic import op
import sqlalchemy as sa

revision = 'd4f61b8e35ca'
down_revision = 'c3e58a7d24bf'
branch_labels = None
depends_on = None

# (module_key, title, description, required_roles) — the roles are each
# script's `audience:`, verbatim.
#
# ⚠️ THE FIRST ENTRY IS A REPAIR, NOT A NEW MODULE, and it is rule 15's second
# half caught in the wild. Slice 10b's migration (`e7f2a4c916b8`) DEFINES
# `data_upgrade(conn)` to seed `ocr_workflow_v1` and never CALLS it from
# `upgrade()`. `cutover_migrate.py` replays data steps, so a cutover-built
# production box has the row and every alembic-upgraded box does not — this
# machine's own mirror had `training_modules` with three rows and not that one.
#
# It is invisible in the direction that hides it: `training.gate()` finds no
# module, reports no interstitial, and the OCR upload proceeds. The gate is
# soft, so nothing breaks — the feature simply never appears, which is what a
# missing gate looks like and what a WORKING soft gate also looks like.
#
# `verify_data_migration_contract()` does not catch it either: it greps
# `upgrade()` for DML and this migration's `upgrade()` genuinely has none.
# Re-seeding here is idempotent (`ON CONFLICT DO NOTHING`) and repairs every
# box on its next upgrade, which editing the old migration would not do —
# alembic never re-runs an applied revision.
_MODULES = [
    ("ocr_workflow_v1",
     "OCR Workflow & Paper Form Alignment",
     "How the printed consumption form is filled, photographed and read — and "
     "what to do when a number comes back blank.",
     "supervisor,store_keeper"),
    ("sk_stage_return_v1",
     "Staging a return",
     "How a return is raised against the receipt it came from, and why the "
     "source receipt is the part that matters.",
     "store_keeper"),
    ("hub_assistant_v1",
     "Asking the Hub Assistant",
     "What the assistant can answer, where its answers come from, and why it "
     "will not tell you about a part of the system you do not use.",
     "store_keeper"),
    ("hod_executive_summary_v1",
     "Reading the Executive Summary",
     "The board brief, and the one number that refuses to lie — why "
     "un-costed stock is counted and labelled rather than valued at zero.",
     "hod"),
]


def data_upgrade(conn) -> None:
    for key, title, desc, roles in _MODULES:
        # ⚠️ `ocr_workflow_v1` KEEPS ITS OWN `gates_feature`. It is the only
        # module that gates anything, and the other three deliberately gate
        # nothing — naming a feature on a walk-through would put an
        # interstitial in front of a screen nobody asked to be stopped at.
        gate = "ocr_upload" if key == "ocr_workflow_v1" else None
        conn.execute(sa.text(
            "INSERT INTO training_modules "
            "  (module_key, title, description, version, required_roles, "
            "   gates_feature, active, created_by) "
            "VALUES (:k, :t, :d, 1, :r, :g, 1, 'phase-13c') "
            "ON CONFLICT (module_key) DO NOTHING"),
            {"k": key, "t": title, "d": desc, "r": roles, "g": gate})


def upgrade() -> None:
    data_upgrade(op.get_bind())


def downgrade() -> None:
    # ⚠️ `ocr_workflow_v1` IS NOT DROPPED. This migration re-seeds it as a
    # repair for a row slice 10b meant to create; removing it here would take
    # away something an earlier migration owns.
    op.execute(
        "DELETE FROM training_modules WHERE module_key IN "
        "('sk_stage_return_v1', 'hub_assistant_v1', 'hod_executive_summary_v1')")
