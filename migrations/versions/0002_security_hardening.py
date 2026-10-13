"""Session tokens, login throttling, one device per student, single attendance index.

Revision ID: 0002_security_hardening
Revises: 0001_baseline
Create Date: 2026-10-12

"""
import secrets

from alembic import op
import sqlalchemy as sa

revision = '0002_security_hardening'
down_revision = '0001_baseline'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # Admin: per-account session token (rotated on password change/reset/deactivation).
    # Lockout state moves to login_throttle, keyed per account and client IP.
    admin_columns = {column['name'] for column in inspector.get_columns('admin')}
    if 'session_token' not in admin_columns:
        op.add_column('admin', sa.Column('session_token', sa.String(64), nullable=True))
    for (admin_id,) in bind.execute(sa.text("SELECT id FROM admin WHERE session_token IS NULL")).fetchall():
        bind.execute(
            sa.text("UPDATE admin SET session_token = :token WHERE id = :id"),
            {'token': secrets.token_hex(16), 'id': admin_id},
        )
    with op.batch_alter_table('admin') as batch:
        batch.alter_column('session_token', existing_type=sa.String(64), nullable=False)
        if 'failed_login_attempts' in admin_columns:
            batch.drop_column('failed_login_attempts')
        if 'locked_until' in admin_columns:
            batch.drop_column('locked_until')

    op.create_table(
        'login_throttle',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('key', sa.String(255), nullable=False, unique=True),
        sa.Column('username', sa.String(120), nullable=False),
        sa.Column('failures', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('window_started_at', sa.DateTime(), nullable=True),
        sa.Column('locked_until', sa.DateTime(), nullable=True),
    )
    op.create_index('ix_login_throttle_username', 'login_throttle', ['username'])

    # A device may be registered to one student only. Devices currently shared by
    # several students are cleared so each student re-registers their own phone.
    bind.execute(sa.text(
        "UPDATE student SET device_id = NULL WHERE device_id IN ("
        "SELECT device_id FROM student WHERE device_id IS NOT NULL "
        "GROUP BY device_id HAVING COUNT(*) > 1)"
    ))
    op.create_index('uq_student_device_id', 'student', ['device_id'], unique=True)

    # Attendance: keep exactly one unique (student_id, session_id) rule. Databases
    # created by older code could have both a table constraint and this index.
    duplicate_constraints = [
        constraint['name'] for constraint in inspector.get_unique_constraints('attendance')
        if set(constraint['column_names']) == {'student_id', 'session_id'} and constraint['name']
    ]
    if duplicate_constraints:
        with op.batch_alter_table('attendance') as batch:
            for name in duplicate_constraints:
                batch.drop_constraint(name, type_='unique')
    index_names = {index['name'] for index in sa.inspect(bind).get_indexes('attendance')}
    if 'uq_attendance_student_session' not in index_names:
        op.create_index('uq_attendance_student_session', 'attendance', ['student_id', 'session_id'], unique=True)


def downgrade():
    op.drop_index('uq_student_device_id', table_name='student')
    op.drop_index('ix_login_throttle_username', table_name='login_throttle')
    op.drop_table('login_throttle')
    with op.batch_alter_table('admin') as batch:
        batch.add_column(sa.Column('failed_login_attempts', sa.Integer(), nullable=False, server_default='0'))
        batch.add_column(sa.Column('locked_until', sa.DateTime(), nullable=True))
        batch.drop_column('session_token')
