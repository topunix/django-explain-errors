"""Thin wrapper around a sqlite-vec vec0 virtual table.

Single-file, no server. One row per indexed chunk: embedding vector plus
file path / line range / chunk text stored as vec0 auxiliary columns.
"""
import sqlite3
import struct

TABLE = "chunks"


def _serialize(vector):
    return struct.pack(f"{len(vector)}f", *vector)


class VectorStore:
    """Context-managed handle onto a sqlite-vec index file.

    Import of `sqlite_vec` is deferred to connection time so callers can
    catch `ImportError` when the optional [rag] extra isn't installed.
    """

    def __init__(self, path):
        self.path = path
        self._conn = None

    def __enter__(self):
        import sqlite_vec

        conn = sqlite3.connect(self.path)
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
        self._conn = conn
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self._conn is not None:
            self._conn.close()
            self._conn = None
        return False

    def create(self, dimensions):
        self._conn.execute(
            f"""
            CREATE VIRTUAL TABLE IF NOT EXISTS {TABLE} USING vec0(
                embedding float[{dimensions}],
                +file_path TEXT,
                +start_line INTEGER,
                +end_line INTEGER,
                +chunk_text TEXT
            )
            """
        )
        self._conn.commit()

    def add(self, chunks):
        """chunks: iterable of (file_path, start_line, end_line, chunk_text, embedding)."""
        self._conn.executemany(
            f"""
            INSERT INTO {TABLE}(embedding, file_path, start_line, end_line, chunk_text)
            VALUES (?, ?, ?, ?, ?)
            """,
            [
                (_serialize(embedding), file_path, start_line, end_line, chunk_text)
                for file_path, start_line, end_line, chunk_text, embedding in chunks
            ],
        )
        self._conn.commit()

    def query(self, embedding, top_k):
        rows = self._conn.execute(
            f"""
            SELECT file_path, start_line, end_line, chunk_text, distance
            FROM {TABLE}
            WHERE embedding MATCH ? AND k = ?
            ORDER BY distance
            """,
            (_serialize(embedding), top_k),
        ).fetchall()
        return [
            {
                "file_path": file_path,
                "start_line": start_line,
                "end_line": end_line,
                "chunk_text": chunk_text,
                "distance": distance,
            }
            for file_path, start_line, end_line, chunk_text, distance in rows
        ]

    def count(self):
        return self._conn.execute(f"SELECT COUNT(*) FROM {TABLE}").fetchone()[0]
