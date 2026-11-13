"""Rename the account table and role to what they are: user_account, and 'principal'.

The table held every login (students included) but was called "admin", and the
Principal's role was stored as 'super_admin'. The role also loses its database
default: a row must always say which role it is, and a forgotten role should fail,
not silently create a Principal.

Revision ID: 0009_user_account
Revises: 0008_student_class_history
Create Date: 2026-11-13

"""
import sqlalchemy as sa
from alembic import op

revision = '0009_user_account'
down_revision = '0008_student_class_history'
branch_labels = None
depends_on = None


def upgrade():
    # SQLite (3.26+) and PostgreSQL also repoint the foreign keys of other tables.
    op.rename_table('admin', 'user_account')
    with op.batch_alter_table('user_account') as batch:
        batch.alter_column('role', existing_type=sa.String(20), existing_nullable=False, server_default=None)
    op.execute("UPDATE user_account SET role = 'principal' WHERE role = 'super_admin'")

    # Two batches: on SQLite, a foreign key created in the same batch that renames its
    # column is silently left out of the rebuilt table.
    with op.batch_alter_table('attendance') as batch:
        batch.alter_column('marked_by_admin_id', new_column_name='marked_by_user_id',
                           existing_type=sa.Integer(), existing_nullable=True)
    with op.batch_alter_table('attendance') as batch:
        batch.drop_constraint('fk_attendance_marked_by_admin', type_='foreignkey')
        batch.create_foreign_key(
            'fk_attendance_marked_by_user', 'user_account', ['marked_by_user_id'], ['id'], ondelete='SET NULL',
        )

    op.drop_index('ix_activity_log_actor_admin_id', table_name='activity_log')
    with op.batch_alter_table('activity_log') as batch:
        batch.alter_column('actor_admin_id', new_column_name='actor_user_id',
                           existing_type=sa.Integer(), existing_nullable=True)
    op.create_index('ix_activity_log_actor_user_id', 'activity_log', ['actor_user_id'])
    op.execute("UPDATE activity_log SET target_type = 'user' WHERE target_type = 'admin'")


def downgrade():
    op.execute("UPDATE activity_log SET target_type = 'admin' WHERE target_type = 'user'")
    op.drop_index('ix_activity_log_actor_user_id', table_name='activity_log')
    with op.batch_alter_table('activity_log') as batch:
        batch.alter_column('actor_user_id', new_column_name='actor_admin_id',
                           existing_type=sa.Integer(), existing_nullable=True)
    op.create_index('ix_activity_log_actor_admin_id', 'activity_log', ['actor_admin_id'])

    with op.batch_alter_table('attendance') as batch:
        batch.alter_column('marked_by_user_id', new_column_name='marked_by_admin_id',
                           existing_type=sa.Integer(), existing_nullable=True)
    with op.batch_alter_table('attendance') as batch:
        batch.drop_constraint('fk_attendance_marked_by_user', type_='foreignkey')
        batch.create_foreign_key(
            'fk_attendance_marked_by_admin', 'user_account', ['marked_by_admin_id'], ['id'], ondelete='SET NULL',
        )

    op.execute("UPDATE user_account SET role = 'super_admin' WHERE role = 'principal'")
    with op.batch_alter_table('user_account') as batch:
        batch.alter_column('role', existing_type=sa.String(20), existing_nullable=False, server_default='super_admin')
    op.rename_table('user_account', 'admin')
