"""Connection and initialization of the independent v0.2.1 research database."""
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import sqlite3

ROOT = Path(__file__).resolve().parents[2]
DDL = ROOT / "docs/phase1b_collection_dataset_schema/schema/marketlens_phase1b_schema_v0_2_1_candidate.sql"
DEFAULT_DB = ROOT / "local_data/phase1c_smoke/research_smoke.db"


def connect(path: Path, *, readonly: bool = False) -> sqlite3.Connection:
    path = Path(path).resolve()
    if path == (ROOT / "marketlens.db").resolve() or path.name == "marketlens.db":
        raise ValueError("Legacy database is outside research scope")
    if not readonly:
        path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(f"{path.as_uri()}?mode={'ro' if readonly else 'rwc'}", uri=True,
                           isolation_level=None)
    conn.row_factory = sqlite3.Row
    for name in ("foreign_keys", "recursive_triggers"):
        conn.execute(f"PRAGMA {name}=ON")
        if conn.execute(f"PRAGMA {name}").fetchone()[0] != 1:
            conn.close()
            raise RuntimeError("Required SQLite connection pragma unavailable")
    return conn


@contextmanager
def transaction(conn: sqlite3.Connection):
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise


def _schema(conn):
    return [tuple(r) for r in conn.execute("SELECT type,name,sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY type,name")]


def initialize(path: Path = DEFAULT_DB, *, reset: bool = False) -> sqlite3.Connection:
    path = Path(path).resolve()
    if reset:
        if path != DEFAULT_DB.resolve():
            raise ValueError("Reset is allowed only for the exact smoke database")
        if path.exists():
            if any(Path(str(path)+suffix).exists() for suffix in ("-wal", "-shm", "-journal")):
                raise ValueError("Refuse reset with live SQLite sidecars")
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            path.rename(path.with_name(f"research_smoke.{stamp}.backup.db"))
    ddl = DDL.read_text(encoding="utf-8")  # Missing candidate must be reported, never reconstructed.
    conn = connect(path)
    try:
        existing = _schema(conn)
        if not existing:
            conn.executescript(ddl)
        with sqlite3.connect(":memory:") as expected:
            expected.executescript(ddl)
            if _schema(conn) != _schema(expected):
                raise ValueError("Existing database schema differs from the candidate; refusing migration")
        if len(conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()) != 18:
            raise ValueError("Expected exactly 18 tables")
        assert_integrity(conn)
        return conn
    except BaseException:
        conn.close()
        raise


def assert_integrity(conn: sqlite3.Connection) -> dict:
    foreign = [tuple(r) for r in conn.execute("PRAGMA foreign_key_check")]
    integrity = [r[0] for r in conn.execute("PRAGMA integrity_check")]
    if foreign or integrity != ["ok"]:
        raise ValueError("SQLite integrity validation failed")
    return {"foreign_key_check": foreign, "integrity_check": integrity}
