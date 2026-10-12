import sqlite3

DB_URL = "db.db"


def init_db(db_url: str = DB_URL):
    with sqlite3.connect(db_url, uri=True) as conn:
        cursor = conn.cursor()
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS files (
            id UUID PRIMARY KEY,
            name TEXT NOT NULL,
            object_storage_name TEXT NULL,
            parent_id UUID REFERENCES files(id),
            is_file BOOLEAN NOT NULL CHECK (is_file IN (0, 1))
        )
        """)


async def get_db():
    with sqlite3.connect(DB_URL) as conn:
        yield conn.cursor()
