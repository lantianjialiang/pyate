"""Atomic touchdown commits for crash recovery and output reconstruction."""
from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path

from .context import PartResult


class RunJournal:
    """Persist only complete touchdown transactions, bound to one input fingerprint."""

    def __init__(self, path: Path, fingerprint: str, *, reset: bool = False) -> None:
        self.connection = sqlite3.connect(path)
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA synchronous=FULL")
        self.connection.execute("CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT)")
        self.connection.execute("CREATE TABLE IF NOT EXISTS touchdowns (unit TEXT, touch INTEGER, payload TEXT, PRIMARY KEY(unit,touch))")
        if reset:
            with self.connection:
                self.connection.execute("DELETE FROM touchdowns")
                self.connection.execute("DELETE FROM metadata")
        row = self.connection.execute("SELECT value FROM metadata WHERE key='fingerprint'").fetchone()
        if row is not None and row[0] != fingerprint:
            self.connection.close()
            raise ValueError("resume input mismatch: configuration, program or pattern changed")
        with self.connection:
            self.connection.execute("INSERT OR IGNORE INTO metadata VALUES ('fingerprint',?)", (fingerprint,))

    def get(self, unit: str, touch: int) -> list[PartResult] | None:
        row = self.connection.execute("SELECT payload FROM touchdowns WHERE unit=? AND touch=?", (unit, touch)).fetchone()
        return None if row is None else [PartResult.from_dict(p) for p in json.loads(row[0])]

    def commit(self, unit: str, touch: int, parts: list[PartResult]) -> None:
        data = json.dumps([p.to_dict(include_results=True) for p in parts], allow_nan=False)
        with self.connection:
            self.connection.execute("INSERT OR REPLACE INTO touchdowns VALUES (?,?,?)", (unit, touch, data))

    def close(self) -> None:
        self.connection.close()

    @staticmethod
    def read_parts(path: Path, unit: str, *, offset: int = 0,
                   limit: int | None = None) -> Iterator[PartResult]:
        """Stream committed history; callers may request bounded pages."""
        if offset < 0 or (limit is not None and limit < 0):
            raise ValueError("history offset/limit must be nonnegative")
        if limit == 0:
            return
        connection = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
        try:
            rows = connection.execute("SELECT payload FROM touchdowns WHERE unit=? ORDER BY touch", (unit,))
            count = 0
            for (payload,) in rows:
                for raw in json.loads(payload):
                    if raw.get("skipped"):
                        continue
                    if offset:
                        offset -= 1
                        continue
                    yield PartResult.from_dict(raw)
                    count += 1
                    if limit is not None and count >= limit:
                        return
        finally:
            connection.close()
