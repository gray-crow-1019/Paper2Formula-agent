from contextlib import contextmanager

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from .config import ROOT
from .models import Base


class Database:
    def __init__(self, settings):
        url = make_url(settings.database_url)
        options = {"pool_pre_ping": True}
        if url.drivername.startswith("sqlite"):
            if url.database and url.database != ":memory:":
                path = (ROOT / url.database).resolve()
                path.parent.mkdir(parents=True, exist_ok=True)
                url = url.set(database=str(path))
            options["connect_args"] = {"check_same_thread": False, "timeout": 30}
        self.engine = create_engine(url, **options)
        if url.drivername.startswith("sqlite"):
            @event.listens_for(self.engine, "connect")
            def configure_sqlite(conn, record):
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA foreign_keys=ON")
        self.sessions = sessionmaker(self.engine, expire_on_commit=False)

    def initialize(self):
        Base.metadata.create_all(self.engine)
        # Additive compatibility for databases created during the first-version implementation.
        additions = {"documents": {"deleted_at": "FLOAT"},
                     "tasks": {"model_calls": "INTEGER NOT NULL DEFAULT 0",
                               "run_deadline": "FLOAT NOT NULL DEFAULT 0"}}
        with self.engine.begin() as connection:
            inspector = inspect(connection)
            for table, columns in additions.items():
                existing = {column["name"] for column in inspector.get_columns(table)}
                for name, definition in columns.items():
                    if name not in existing:
                        connection.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {definition}"))

    @contextmanager
    def session(self):
        with self.sessions() as session:
            try:
                yield session
                session.commit()
            except BaseException:
                session.rollback()
                raise
