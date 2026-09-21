"""rename_ai_clinical_summary_to_patient_intake_summary

Revision ID: b80445ff8eb5
Revises: 2e5c82fd60c7
Create Date: 2026-09-19 13:56:18.980288

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b80445ff8eb5'
down_revision: Union[str, Sequence[str], None] = '2e5c82fd60c7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('questionnaire_responses') as batch_op:
        batch_op.alter_column('ai_clinical_summary', new_column_name='patient_intake_summary', existing_type=sa.Text())
    with op.batch_alter_table('pre_visit_questionnaires') as batch_op:
        batch_op.alter_column('ai_clinical_summary', new_column_name='patient_intake_summary', existing_type=sa.Text())


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('questionnaire_responses') as batch_op:
        batch_op.alter_column('patient_intake_summary', new_column_name='ai_clinical_summary', existing_type=sa.Text())
    with op.batch_alter_table('pre_visit_questionnaires') as batch_op:
        batch_op.alter_column('patient_intake_summary', new_column_name='ai_clinical_summary', existing_type=sa.Text())
