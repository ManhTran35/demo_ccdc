"""Byte-object stores with compare-and-swap writes.

Local storage is a development-only SQLite object store. A single transaction
commits an object's bytes and generation, including across Python processes.
Production uses GCS generations and Application Default Credentials.
"""

from __future__ import annotations

from dataclasses import dataclass
from contextlib import closing
from pathlib import Path
import sqlite3
from typing import Protocol


class StorageError(RuntimeError):
    """An object-store operation failed without exposing backend details to UI."""


class ObjectNotFound(StorageError):
    pass


class PreconditionFailed(StorageError):
    """The caller's generation is stale (zero means create-only)."""


@dataclass(frozen=True)
class ObjectData:
    data: bytes
    generation: int


class StorageAdapter(Protocol):
    def get(self, key: str) -> ObjectData: ...

    def put(self, key: str, data: bytes, *, if_generation_match: int | None = None,
            content_type: str = "application/octet-stream") -> int: ...

    def list(self, prefix: str = "") -> list[str]: ...


def validate_key(key: str, *, prefix: bool = False) -> str:
    """Accept only unambiguous relative object names, also for local storage."""
    if not isinstance(key, str):
        raise ValueError("Object key must be a string")
    if prefix and key == "":
        return key
    candidate = key[:-1] if prefix and key.endswith("/") else key
    if (not candidate or candidate.startswith("/") or "\\" in candidate
            or any(ord(c) < 32 or ord(c) == 127 for c in candidate)
            or any(part in ("", ".", "..") for part in candidate.split("/"))
            or len(key.encode("utf-8")) > 1024):
        raise ValueError("Object key must be a safe relative path")
    return key


def _generation(value: int | None) -> None:
    if value is not None and (type(value) is not int or value < 0):
        raise ValueError("Generation must be a nonnegative integer or None")


class LocalStorageAdapter:
    """Durable local adapter; do not use on Cloud Run's ephemeral filesystem."""

    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.database = self.root / "objects.sqlite3"
        with closing(self._connect()) as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("""CREATE TABLE IF NOT EXISTS objects (
                key TEXT PRIMARY KEY, data BLOB NOT NULL,
                generation INTEGER NOT NULL CHECK (generation > 0),
                content_type TEXT NOT NULL)""")

    def _connect(self):
        connection = sqlite3.connect(self.database, timeout=30, isolation_level=None)
        connection.execute("PRAGMA synchronous=FULL")
        return connection

    def get(self, key: str) -> ObjectData:
        validate_key(key)
        with closing(self._connect()) as connection:
            row = connection.execute("SELECT data, generation FROM objects WHERE key=?", (key,)).fetchone()
        if row is None:
            raise ObjectNotFound(f"Object not found: {key}")
        return ObjectData(bytes(row[0]), int(row[1]))

    def put(self, key: str, data: bytes, *, if_generation_match: int | None = None,
            content_type: str = "application/octet-stream") -> int:
        validate_key(key)
        _generation(if_generation_match)
        if not isinstance(data, bytes):
            raise TypeError("Object content must be bytes")
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute("SELECT generation FROM objects WHERE key=?", (key,)).fetchone()
                current = 0 if row is None else int(row[0])
                if if_generation_match is not None and if_generation_match != current:
                    raise PreconditionFailed("Object changed; reload its current generation")
                generation = current + 1
                connection.execute("""INSERT INTO objects (key,data,generation,content_type)
                    VALUES (?,?,?,?) ON CONFLICT(key) DO UPDATE SET
                    data=excluded.data,generation=excluded.generation,content_type=excluded.content_type""",
                    (key, data, generation, content_type))
                connection.execute("COMMIT")
            except BaseException:
                connection.execute("ROLLBACK")
                raise
        return generation

    def list(self, prefix: str = "") -> list[str]:
        validate_key(prefix, prefix=True)
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT key FROM objects WHERE substr(key,1,?)=? ORDER BY key",
                (len(prefix), prefix)).fetchall()
        return [row[0] for row in rows]


class GCSStorageAdapter:
    """Private GCS objects via ADC; caller-supplied client supports isolated tests."""

    def __init__(self, bucket: str, prefix: str = "", client=None):
        if not isinstance(bucket, str) or not bucket or "/" in bucket:
            raise ValueError("A GCS bucket name is required")
        validate_key(prefix, prefix=True)
        self.prefix = prefix.rstrip("/") + "/" if prefix else ""
        if client is None:
            from google.cloud import storage
            client = storage.Client()
        self.client = client
        self.bucket = client.bucket(bucket)

    def _name(self, key: str, *, prefix: bool = False) -> str:
        validate_key(key, prefix=prefix)
        name = self.prefix + key
        if len(name.encode("utf-8")) > 1024:
            raise ValueError("Object name exceeds GCS limit")
        return name

    @staticmethod
    def _raise(error: Exception):
        # Google exceptions retain backend response/body. Do not expose those to UI.
        code = getattr(error, "code", None)
        if callable(code):
            code = code()
        if code == 404 or type(error).__name__ == "NotFound":
            raise ObjectNotFound("GCS object not found") from error
        if code == 412 or type(error).__name__ == "PreconditionFailed":
            raise PreconditionFailed("Object changed; reload its current generation") from error
        raise StorageError("GCS object operation failed") from error

    def get(self, key: str) -> ObjectData:
        blob = self.bucket.blob(self._name(key))
        try:
            # Bind download bytes to the generation observed by reload. Never pair
            # bytes from one concurrent write with metadata from another write.
            blob.reload()
            generation = int(blob.generation)
            data = blob.download_as_bytes(if_generation_match=generation, checksum="auto")
            return ObjectData(data, generation)
        except Exception as error:
            self._raise(error)

    def put(self, key: str, data: bytes, *, if_generation_match: int | None = None,
            content_type: str = "application/octet-stream") -> int:
        _generation(if_generation_match)
        if not isinstance(data, bytes):
            raise TypeError("Object content must be bytes")
        blob = self.bucket.blob(self._name(key))
        try:
            blob.upload_from_string(data, content_type=content_type,
                                    if_generation_match=if_generation_match, checksum="auto")
            return int(blob.generation)
        except Exception as error:
            self._raise(error)

    def list(self, prefix: str = "") -> list[str]:
        name = self._name(prefix, prefix=True)
        try:
            result = []
            for blob in self.client.list_blobs(self.bucket, prefix=name):
                if not blob.name.startswith(self.prefix):
                    raise StorageError("Object outside configured prefix")
                key = blob.name[len(self.prefix):]
                validate_key(key)
                result.append(key)
            return sorted(result)
        except ValueError:
            raise
        except Exception as error:
            self._raise(error)
