"""Activity log descriptions as TEXT, so a long reason is no longer cut off at 255 characters.

The reason for a correction comes last in its description ("... Reason: phone battery died"),
so the 255-character limit cut off exactly the part worth keeping. Existing entries keep their
text unchanged, so the audit chain still verifies.

Revision ID: 0026_activity_log_text
Revises: 0025_subject_color
Create Date: 2026-11-19

"""
import sqlalchemy as sa
from alembic import op

revision = '0026_activity_log_text'
down_revision = '0025_subject_color'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('activity_log') as batch_op:
        batch_op.alter_column('description', existing_type=sa.String(length=255), type_=sa.Text(),
                              existing_nullable=False)


def downgrade():
    # Shortening sealed entries would break the audit chain, so refuse instead of cutting them.
    longer = op.get_bind().execute(
        sa.text('SELECT COUNT(*) FROM activity_log WHERE LENGTH(description) > 255')
    ).scalar()
    if longer:
        raise RuntimeError(
            f"{longer} activity log entr(ies) are longer than 255 characters. Downgrading would cut them "
            "and break the audit chain, so it was stopped."
        )
    with op.batch_alter_table('activity_log') as batch_op:
        batch_op.alter_column('description', existing_type=sa.Text(), type_=sa.String(length=255),
                              existing_nullable=False)
