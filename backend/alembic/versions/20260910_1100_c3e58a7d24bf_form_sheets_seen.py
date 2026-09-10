"""sme_consumption_form.Sheets_Seen — which PAGES of a form have come back

Revision ID: c3e58a7d24bf
Revises: b2d47f6c91ae
Create Date: 2026-09-10 11:00:00

Phase 13b. A consumption form of more than 18 materials prints on several A4
pages, and until now every one of those pages carried the SAME QR payload.

⚠️ WHICH MEANT A MULTI-PAGE FORM COULD NOT BE FILED AT ALL.

The reader identifies a sheet by its QR. With one payload across three pages it
could not tell page 2 from page 1, so:

  * photographing page 2 after page 1 was refused as "already filed" — true of
    the FORM and false of the PAGE, and the supervisor had no way whatsoever to
    record rows 19 to 36; and
  * photographing ONLY page 2 opened a draft with page 1's eighteen rows
    silently set to zero, which reports eighteen materials as unused and looks
    entirely plausible on the way to an approval.

`GIF2` adds the page number and the page count to the payload, and this column
records which of those pages have arrived. The form's identity is unchanged —
all pages still share one `Form_UUID`, one fingerprint and one execution entry.

⚠️ DO NOT CONFUSE THIS WITH `Batch_Seq` (13a). `Batch_Seq` is which FORM of a
print run this is — sheet 7 of 50 forms. `Sheets_Seen` is which A4 PAGES of
THIS ONE form have been photographed. A 50-form run of a 36-material recipe is
50 rows here, each of which will eventually read "1,2".

Stored as a comma-separated list of page numbers rather than a count, because
"sheets 1 and 3 are in, 2 is missing" is what the person holding the phone
needs told; a count of 2 out of 3 does not say which page to go and fetch.

────────────────────────────────────────────────────────────────────────────
NULL ON EVERY EXISTING ROW, AND THAT IS THE CORRECT READING.

Every form printed before this carried a `GIF1` payload, which `parse_qr` reads
as **sheet 1 of 1** — what it always meant. A NULL therefore says "no page has
been recorded by number", and `_sheets_seen()` returns the empty set, which
makes an old single-page form behave exactly as it did: consumed once, refused
thereafter. Back-filling "1" onto historical rows would claim page 1 of a
multi-page form had been read when the system never knew which page it got.

DDL only — no `data_upgrade()` is required, and the cutover contract verifier
agrees. The column is declared in `backend/models.py` in the SAME commit
(rule 15's second half): `cutover_migrate.py` builds production with
`metadata.create_all`, so a column living only here would be absent from every
production box.
"""
from alembic import op
import sqlalchemy as sa

revision = 'c3e58a7d24bf'
down_revision = 'b2d47f6c91ae'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('sme_consumption_form',
                  sa.Column('Sheets_Seen', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('sme_consumption_form', 'Sheets_Seen')
