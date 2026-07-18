"""sme_sap_codes — SAP_Code join keys on the SME seed tables

2026-07-18 workbook overhaul: the operator added SAP_Code columns to
For_1_SQM.xlsx and Materials_DetailsAvailable_Qty.xlsx so SME recipe lines
map to exact ERP inventory items (incl. the variant SAPs 1041-1/-2/-3 that
one Material_Code cannot distinguish — PU systems carry four component rows
per material). Recipe line identity therefore widens from
(Lining_System_Code, Material_Code) to (code, material, SAP_Code).

Revision ID: b3f2a9c47d18
Revises: e7c31a9f24d5
Create Date: 2026-07-18 11:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b3f2a9c47d18'
down_revision: Union[str, None] = 'e7c31a9f24d5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('sme_recipe', sa.Column('SAP_Code', sa.Text(), nullable=True))
    op.add_column('sme_inventory_seed',
                  sa.Column('SAP_Code', sa.Text(), nullable=True))
    op.drop_constraint('sme_recipe_Lining_System_Code_Material_Code_key',
                       'sme_recipe', type_='unique')
    op.create_unique_constraint(
        'sme_recipe_code_mat_sap_key', 'sme_recipe',
        ['Lining_System_Code', 'Material_Code', 'SAP_Code'])


def downgrade() -> None:
    op.drop_constraint('sme_recipe_code_mat_sap_key', 'sme_recipe',
                       type_='unique')
    op.create_unique_constraint(
        'sme_recipe_Lining_System_Code_Material_Code_key', 'sme_recipe',
        ['Lining_System_Code', 'Material_Code'])
    op.drop_column('sme_inventory_seed', 'SAP_Code')
    op.drop_column('sme_recipe', 'SAP_Code')
