import sqlite3


def init_db():
    with sqlite3.connect("db.db") as conn:
        cursor = conn.cursor()
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS files (
            id UUID PRIMARY KEY,
            name TEXT NOT NULL,
            object_storage_name TEXT NOT NULL,
            parent_id UUID REFERENCES files(id),
            is_file BOOLEAN NOT NULL CHECK (is_file IN (0, 1))
        )
        """)


async def get_db():
    with sqlite3.connect("db.db") as conn:
        yield conn.cursor()
