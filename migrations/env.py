from alembic import context
from flask import current_app

config = context.config


def get_engine():
    return current_app.extensions['migrate'].db.engine


def get_metadata():
    return current_app.extensions['migrate'].db.metadata


config.set_main_option('sqlalchemy.url', get_engine().url.render_as_string(hide_password=False).replace('%', '%%'))


def run_migrations_offline():
    context.configure(
        url=config.get_main_option('sqlalchemy.url'),
        target_metadata=get_metadata(),
        literal_binds=True,
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    conf_args = dict(current_app.extensions['migrate'].configure_args)
    conf_args.setdefault('render_as_batch', True)

    with get_engine().connect() as connection:
        is_sqlite = connection.dialect.name == 'sqlite'
        if is_sqlite:
            # SQLite batch migrations rebuild tables (copy, DROP, rename). With foreign
            # keys on, the DROP would fire ON DELETE CASCADE/SET NULL on child rows.
            connection.exec_driver_sql('PRAGMA foreign_keys=OFF')
            connection.commit()
        try:
            context.configure(connection=connection, target_metadata=get_metadata(), **conf_args)
            with context.begin_transaction():
                context.run_migrations()
        finally:
            if is_sqlite:
                connection.exec_driver_sql('PRAGMA foreign_keys=ON')
                connection.commit()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
