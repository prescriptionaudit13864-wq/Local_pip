"""Offline ICD-11 MMS terminology search for the PrescribeX diagnosis field.

The source is WHO's Simple Tabulation release.  Only coded ``category`` rows
are offered to the prescriber; chapters and non-coded grouping blocks remain
classification structure rather than selectable diagnoses.
"""

from __future__ import annotations

import csv
import os
import re
import sqlite3
import tempfile
import zipfile
from contextlib import closing
from pathlib import Path
from typing import Any

RELEASE_ID = "2026-01"
LANGUAGE = "en"
_PACKAGE = Path(__file__).resolve().parent
DEFAULT_SOURCE = _PACKAGE / "data" / "SimpleTabulation-ICD-11-MMS-en-2026-01.zip"
DEFAULT_DATABASE = _PACKAGE / "data" / "icd11_mms_2026_01.sqlite3"
SOURCE_URL = "https://icdcdn.who.int/static/releasefiles/2026-01/SimpleTabulation-ICD-11-MMS-en.zip"
_TABULATION_FILE = "SimpleTabulation-ICD-11-MMS-en.txt"


def _clean(value: Any) -> str:
    return str(value or "").strip().lstrip("\ufeff")


def _clean_title(value: Any) -> str:
    return re.sub(r"^\s*(?:-\s*)+", "", _clean(value)).strip()


def _bool(value: Any) -> int:
    return int(_clean(value).casefold() == "true")


def build_database(source: str | Path = DEFAULT_SOURCE, target: str | Path = DEFAULT_DATABASE) -> int:
    """Build an atomic SQLite search database; return the number of diagnoses."""
    source_path, target_path = Path(source), Path(target)
    if not source_path.is_file():
        raise FileNotFoundError(f"ICD-11 source package is missing: {source_path}. Download it from {SOURCE_URL}")
    target_path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{target_path.name}.", dir=target_path.parent)
    os.close(fd)
    temp_path = Path(temp_name)
    count = 0
    try:
        with closing(sqlite3.connect(temp_path)) as con, con:
            con.executescript(
                """
                PRAGMA journal_mode=OFF;
                PRAGMA synchronous=OFF;
                CREATE TABLE diagnoses (
                    code TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    title_norm TEXT NOT NULL,
                    foundation_uri TEXT NOT NULL,
                    linearization_uri TEXT NOT NULL,
                    parent_uri TEXT,
                    chapter_no TEXT,
                    depth_in_kind INTEGER,
                    is_residual INTEGER NOT NULL DEFAULT 0,
                    is_leaf INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                """
            )
            with zipfile.ZipFile(source_path) as archive, archive.open(_TABULATION_FILE) as raw:
                import io

                reader = csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8-sig"), delimiter="\t")
                rows = []
                for row in reader:
                    code = _clean(row.get("Code"))
                    title = _clean_title(row.get("Title"))
                    if _clean(row.get("ClassKind")).casefold() != "category" or not code or not title:
                        continue
                    try:
                        depth = int(_clean(row.get("DepthInKind")) or 0)
                    except ValueError:
                        depth = 0
                    rows.append(
                        (
                            code,
                            title,
                            title.casefold(),
                            _clean(row.get("Foundation URI")),
                            _clean(row.get("Linearization URI")),
                            _clean(row.get("Parent")),
                            _clean(row.get("ChapterNo")),
                            depth,
                            _bool(row.get("IsResidual")),
                            _bool(row.get("isLeaf")),
                        )
                    )
                con.executemany("INSERT OR REPLACE INTO diagnoses VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
                count = len(rows)
            con.executemany(
                "INSERT INTO metadata(key, value) VALUES (?, ?)",
                (
                    ("release", RELEASE_ID),
                    ("language", LANGUAGE),
                    ("linearization", "mms"),
                    ("source_url", SOURCE_URL),
                    ("records", str(count)),
                ),
            )
            con.execute("CREATE INDEX idx_diagnoses_title_norm ON diagnoses(title_norm)")
            con.commit()
        temp_path.replace(target_path)
    finally:
        temp_path.unlink(missing_ok=True)
    return count


class ICD11Terminology:
    """Small read-only facade around the locally generated ICD-11 database."""

    def __init__(self, database: str | Path = DEFAULT_DATABASE, source: str | Path = DEFAULT_SOURCE) -> None:
        self.database = Path(database)
        self.source = Path(source)

    def ensure_ready(self) -> None:
        if not self.database.is_file():
            build_database(self.source, self.database)

    def status(self) -> dict[str, Any]:
        self.ensure_ready()
        with closing(sqlite3.connect(self.database)) as con, con:
            metadata = dict(con.execute("SELECT key, value FROM metadata"))
        return {"ready": True, **metadata, "database": str(self.database)}

    def search(self, query: Any, limit: int = 12) -> list[dict[str, Any]]:
        self.ensure_ready()
        q = " ".join(_clean(query).casefold().split())
        if not q:
            return []
        limit = max(1, min(int(limit), 25))
        tokens = [token for token in re.findall(r"[a-z0-9]+", q) if token]
        if not tokens:
            return []
        where = " AND ".join("title_norm LIKE ?" for _ in tokens)
        sql = f"""
            SELECT code, title, foundation_uri, linearization_uri, parent_uri,
                   chapter_no, is_residual, is_leaf
              FROM diagnoses
             WHERE ({where}) OR upper(code) LIKE ?
             ORDER BY CASE
                        WHEN upper(code) = ? THEN 0
                        WHEN upper(code) LIKE ? THEN 1
                        WHEN title_norm = ? THEN 2
                        WHEN title_norm LIKE ? THEN 3
                        ELSE 4
                      END,
                      is_residual ASC, length(title) ASC, title ASC
             LIMIT ?
        """
        # The first code LIKE belongs to WHERE; duplicate code params support
        # exact/prefix ordering without relying on database-specific aliases.
        where_params = [f"%{token}%" for token in tokens] + [f"{q.upper()}%"]
        order_params = [q.upper(), f"{q.upper()}%", q, f"{q}%", limit]
        with closing(sqlite3.connect(self.database)) as con, con:
            con.row_factory = sqlite3.Row
            rows = con.execute(sql, [*where_params, *order_params]).fetchall()
        return [
            {
                **dict(row),
                "release": RELEASE_ID,
                "language": LANGUAGE,
                "linearization": "mms",
            }
            for row in rows
        ]


__all__ = [
    "DEFAULT_DATABASE",
    "DEFAULT_SOURCE",
    "ICD11Terminology",
    "LANGUAGE",
    "RELEASE_ID",
    "SOURCE_URL",
    "build_database",
]


if __name__ == "__main__":
    total = build_database()
    print(f"Built {DEFAULT_DATABASE} with {total:,} selectable ICD-11 diagnoses ({RELEASE_ID}, {LANGUAGE}).")
