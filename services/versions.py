"""Immutable snapshots and a single generation-protected active pointer.

Crash invariant: snapshots and an immutable audit intent are completely verified
before the active pointer changes. The pointer CAS is the final write. Only audit
events reachable from the active pointer's event chain count as publications;
unreachable intents and prepared versions are harmless orphans, never history.
Thus a process dying immediately after the CAS cannot hide a valid publication,
and a failed concurrent publisher cannot invent a published version.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
import hashlib
import hmac
import json
import re
from typing import Any
import uuid

from services.storage import ObjectNotFound, PreconditionFailed, StorageAdapter


class VersionIntegrityError(RuntimeError):
    pass


class ConfirmationError(ValueError):
    pass


class PublicationUncertain(RuntimeError):
    """Read back state before retrying after an unknown pointer-write outcome."""


def _json(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                       allow_nan=False) + "\n").encode("utf-8")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ_") + uuid.uuid4().hex[:12]


def _valid_id(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"\d{8}T\d{6}Z_[0-9a-f]{12}", value):
        raise ValueError("Invalid version or staging ID")
    return value


def _actor(value: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 320 or any(ord(c) < 32 for c in value):
        raise ValueError("A trusted publisher identity is required")
    return value.strip()


def _expected(value: int) -> int:
    if type(value) is not int or value < 0:
        raise ValueError("Expected active generation must be a nonnegative integer")
    return value


def _parquet_info(data: bytes) -> dict:
    import pyarrow as pa
    import pyarrow.parquet as pq
    try:
        # Read data pages as well as the footer; a valid footer alone is not enough.
        table = pq.read_table(pa.BufferReader(data))
        schema = table.schema
        if table.num_rows == 0 or not schema.names or len(set(schema.names)) != len(schema.names):
            raise ValueError("Dataset must contain rows and unique column names")
        return {"row_count": table.num_rows,
                "schema": [{"name": field.name, "type": str(field.type), "nullable": field.nullable}
                           for field in schema],
                "schema_sha256": _sha(schema.serialize().to_pybytes())}
    except Exception as error:
        raise VersionIntegrityError("Parquet dataset is invalid or unreadable") from error


class VersionManager:
    ACTIVE_KEY = "metadata/active.json"

    def __init__(self, storage: StorageAdapter):
        self.storage = storage

    def _read_json(self, key: str) -> dict:
        try:
            result = json.loads(self.storage.get(key).data)
            if not isinstance(result, dict):
                raise ValueError("JSON object required")
            return result
        except (UnicodeError, ValueError, TypeError) as error:
            raise VersionIntegrityError("Stored metadata is invalid") from error

    def _create(self, key: str, data: bytes, content_type: str) -> None:
        self.storage.put(key, data, if_generation_match=0, content_type=content_type)

    def get_active(self) -> dict | None:
        try:
            obj = self.storage.get(self.ACTIVE_KEY)
        except ObjectNotFound:
            return None
        try:
            pointer = json.loads(obj.data)
            if pointer.get("schema_version") != 1:
                raise ValueError("Unknown active schema")
            _valid_id(pointer["active_version"])
            if not re.fullmatch(r"[0-9a-f]{32}", pointer["event_id"]):
                raise ValueError("Invalid event ID")
            date.fromisoformat(pointer["data_date"])
            pointer["_generation"] = obj.generation
            return pointer
        except (UnicodeError, ValueError, KeyError, TypeError, AttributeError) as error:
            raise VersionIntegrityError("Active pointer is invalid; refusing fallback") from error

    def _active_at(self, expected_generation: int) -> dict | None:
        _expected(expected_generation)
        active = self.get_active()
        actual = active["_generation"] if active else 0
        if actual != expected_generation:
            raise PreconditionFailed("Active version changed; regenerate the preview")
        return active

    def stage_upload(self, content: bytes, filename: str, actor: str) -> dict:
        if not isinstance(content, bytes):
            raise ValueError("Upload content must be bytes")
        actor = _actor(actor)
        if not isinstance(filename, str) or not filename:
            raise ValueError("Upload filename is required")
        # Filename is metadata only, never a filesystem or object path.
        filename = filename.replace("\\", "/").rsplit("/", 1)[-1]
        if not filename or len(filename) > 255 or any(ord(c) < 32 for c in filename):
            raise ValueError("Invalid upload filename")
        stage_id = _id()
        metadata = {"id": stage_id, "source_key": f"staging/{stage_id}/source.xlsx",
                    "filename": filename, "sha256": _sha(content), "size_bytes": len(content),
                    "uploaded_by": actor, "uploaded_at_utc": _now()}
        self._create(metadata["source_key"], content,
                     "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        self._create(f"staging/{stage_id}/metadata.json", _json(metadata), "application/json")
        return metadata

    def get_stage(self, stage_id: str) -> dict:
        _valid_id(stage_id)
        stage = self._read_json(f"staging/{stage_id}/metadata.json")
        if stage.get("id") != stage_id or stage.get("source_key") != f"staging/{stage_id}/source.xlsx":
            raise VersionIntegrityError("Staging metadata does not match its location")
        return stage

    def record_stage_validation(self, stage_id: str, report: dict) -> None:
        """Retain a failed/successful validation without changing active data."""
        self.get_stage(stage_id)
        if not isinstance(report, dict):
            raise ValueError("Validation report must be an object")
        self._create(f"staging/{stage_id}/validation.json", _json(report), "application/json")

    def prepare_version(self, stage: dict, parquet_bytes: bytes, mapping: dict,
                        validation: dict, metrics: dict, data_date: str, actor: str,
                        expected_active_generation: int, note: str = "") -> dict:
        self._active_at(expected_active_generation)
        actor = _actor(actor)
        if not isinstance(data_date, str) or date.fromisoformat(data_date).isoformat() != data_date:
            raise ValueError("Data date must be YYYY-MM-DD")
        if not isinstance(note, str) or len(note) > 2000:
            raise ValueError("Note must be at most 2000 characters")
        if not all(isinstance(value, dict) for value in (mapping, validation, metrics)):
            raise ValueError("Mapping, validation and metrics must be objects")
        if validation.get("valid") is not True or validation.get("errors"):
            raise VersionIntegrityError("Validation did not pass; active version unchanged")
        info = _parquet_info(parquet_bytes)
        if type(validation.get("row_count")) is not int or validation["row_count"] != info["row_count"]:
            raise VersionIntegrityError("Validation and Parquet row counts differ")
        if "rows" in metrics and metrics["rows"] != info["row_count"]:
            raise VersionIntegrityError("Metrics and Parquet row counts differ")
        persisted_stage = self.get_stage(stage["id"])
        if persisted_stage != stage:
            raise VersionIntegrityError("Staging metadata was modified")
        source = self.storage.get(persisted_stage["source_key"]).data
        if _sha(source) != stage["sha256"] or len(source) != stage["size_bytes"]:
            raise VersionIntegrityError("Staging source checksum differs")
        version_id = _id()
        base = f"versions/{version_id}/"
        payloads = {"source": (f"raw/{version_id}/source.xlsx", source,
                               "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
                    "dataset": (base + "inventory.parquet", parquet_bytes, "application/vnd.apache.parquet"),
                    "mapping": (base + "mapping.json", _json(mapping), "application/json"),
                    "validation": (base + "validation.json", _json(validation), "application/json"),
                    "metrics": (base + "metrics.json", _json(metrics), "application/json")}
        classification_stage_id = validation.get("classification_stage_id")
        if classification_stage_id is not None:
            classification_stage = self.get_stage(classification_stage_id)
            classification_source = self.storage.get(classification_stage["source_key"]).data
            if (_sha(classification_source) != classification_stage["sha256"]
                    or len(classification_source) != classification_stage["size_bytes"]):
                raise VersionIntegrityError("Classification staging source checksum differs")
            payloads["classification_source"] = (
                f"raw/{version_id}/classification.xlsx", classification_source,
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        manifest = {"schema_version": 1, "id": version_id, "version_id": version_id,
                    "data_date": data_date, "prepared_at_utc": _now(), "prepared_by": actor,
                    "expected_active_generation": expected_active_generation, "note": note,
                    "stage_id": stage["id"], "source_filename": stage["filename"], **info,
                    "artifacts": {name: {"key": key, "sha256": _sha(data), "size_bytes": len(data)}
                                  for name, (key, data, _) in payloads.items()}}
        if classification_stage_id is not None:
            manifest["classification_stage_id"] = classification_stage_id
        for key, data, content_type in payloads.values():
            self._create(key, data, content_type)
        self._create(base + "manifest.json", _json(manifest), "application/json")
        self._verify_bundle(version_id)
        return self.get_preview(version_id)

    def get_manifest(self, version_id: str) -> dict:
        _valid_id(version_id)
        manifest = self._read_json(f"versions/{version_id}/manifest.json")
        if manifest.get("schema_version") != 1 or manifest.get("id") != version_id:
            raise VersionIntegrityError("Version manifest does not match its location")
        return manifest

    def _artifact(self, version_id: str, manifest: dict, name: str) -> bytes:
        files = {"dataset": f"versions/{version_id}/inventory.parquet",
                 "mapping": f"versions/{version_id}/mapping.json",
                 "validation": f"versions/{version_id}/validation.json",
                 "metrics": f"versions/{version_id}/metrics.json",
                 "source": f"raw/{version_id}/source.xlsx",
                 "classification_source": f"raw/{version_id}/classification.xlsx"}
        try:
            artifact = manifest["artifacts"][name]
            if artifact["key"] != files[name]:
                raise ValueError("Unexpected artifact key")
            data = self.storage.get(artifact["key"]).data
            if len(data) != artifact["size_bytes"] or not hmac.compare_digest(_sha(data), artifact["sha256"]):
                raise ValueError("Checksum mismatch")
            return data
        except (KeyError, ValueError, TypeError) as error:
            raise VersionIntegrityError("Version artifact failed checksum verification") from error

    def read_dataset(self, version_id: str) -> bytes:
        return self._artifact(version_id, self.get_manifest(version_id), "dataset")

    def read_source(self, version_id: str) -> bytes:
        return self._artifact(version_id, self.get_manifest(version_id), "source")

    def read_classification_source(self, version_id: str) -> bytes:
        return self._artifact(version_id, self.get_manifest(version_id), "classification_source")

    def _read_artifact_json(self, version_id: str, name: str) -> dict:
        try:
            result = json.loads(self._artifact(version_id, self.get_manifest(version_id), name))
            if not isinstance(result, dict):
                raise ValueError("JSON object required")
            return result
        except (UnicodeError, ValueError, TypeError) as error:
            raise VersionIntegrityError("Version JSON artifact is invalid") from error

    def read_mapping(self, version_id: str) -> dict:
        return self._read_artifact_json(version_id, "mapping")

    def read_validation(self, version_id: str) -> dict:
        return self._read_artifact_json(version_id, "validation")

    def read_metrics(self, version_id: str) -> dict:
        return self._read_artifact_json(version_id, "metrics")

    def _verify_bundle(self, version_id: str) -> dict:
        manifest = self.get_manifest(version_id)
        contents = {name: self._artifact(version_id, manifest, name)
                    for name in ("source", "dataset", "mapping", "validation", "metrics")}
        if "classification_source" in manifest["artifacts"]:
            self._artifact(version_id, manifest, "classification_source")
        elif manifest.get("classification_stage_id") is not None:
            raise VersionIntegrityError("Classification source artifact is missing")
        info = _parquet_info(contents["dataset"])
        if any(manifest.get(field) != value for field, value in info.items()):
            raise VersionIntegrityError("Parquet row count or schema differs from manifest")
        try:
            validation = json.loads(contents["validation"])
            if validation.get("valid") is not True or validation.get("errors") or validation.get("row_count") != info["row_count"]:
                raise ValueError("Invalid validation result")
            if not all(isinstance(json.loads(contents[name]), dict) for name in ("mapping", "metrics")):
                raise ValueError("JSON object required")
        except (ValueError, TypeError, AttributeError) as error:
            raise VersionIntegrityError("Stored validation or mapping failed readback") from error
        return manifest

    def _token(self, action: str, version_id: str, expected_generation: int) -> str:
        _expected(expected_generation)
        manifest_bytes = self.storage.get(f"versions/{_valid_id(version_id)}/manifest.json").data
        return _sha(_json({"action": action, "version_id": version_id,
                           "manifest_sha256": _sha(manifest_bytes), "expected_active_generation": expected_generation}))

    def get_preview(self, version_id: str) -> dict:
        manifest = self.get_manifest(version_id)
        expected = manifest["expected_active_generation"]
        return {**manifest, "confirmation_token": self._token("publish", version_id, expected),
                "metrics": self.read_metrics(version_id), "validation": self.read_validation(version_id)}

    def _events(self, active: dict | None) -> list[dict]:
        event_id = active["event_id"] if active else None
        seen, events = set(), []
        while event_id:
            if not isinstance(event_id, str) or event_id in seen or not re.fullmatch(r"[0-9a-f]{32}", event_id):
                raise VersionIntegrityError("Publication audit chain is invalid")
            seen.add(event_id)
            event = self._read_json(f"logs/{event_id}.json")
            try:
                if (event.get("schema_version") != 1 or event.get("event_id") != event_id
                        or event.get("action") not in ("publish", "rollback")):
                    raise ValueError("Invalid event metadata")
                version_id = _valid_id(event["version_id"])
                actual_manifest_hash = _sha(self.storage.get(f"versions/{version_id}/manifest.json").data)
                if not hmac.compare_digest(actual_manifest_hash, event["manifest_sha256"]):
                    raise ValueError("Published manifest was modified")
            except (ValueError, TypeError, KeyError) as error:
                raise VersionIntegrityError("Publication audit event or manifest differs") from error
            events.append(event)
            event_id = event.get("previous_event_id")
        if active and (not events or events[0]["version_id"] != active["active_version"]):
            raise VersionIntegrityError("Active pointer and audit chain differ")
        return events

    def list_versions(self) -> list[dict]:
        active = self.get_active()
        events = self._events(active)
        publications = {}
        for offset, event in enumerate(events):
            # Event-chain order is authoritative even when several publications
            # share the same clock timestamp. A rollback gives its target the
            # latest publication rank; failed CAS intents never receive a rank.
            publications.setdefault(event["version_id"], (event, len(events) - offset))
        result = []
        for key in self.storage.list("versions/"):
            if not re.fullmatch(r"versions/\d{8}T\d{6}Z_[0-9a-f]{12}/manifest\.json", key):
                continue
            version_id = key.split("/")[1]
            manifest = self.get_manifest(version_id)
            publication = publications.get(version_id)
            event, publication_order = publication if publication else (None, 0)
            result.append({**manifest, "published": event is not None,
                           "current": bool(active and active["active_version"] == version_id),
                           "publication_order": publication_order,
                           "published_at_utc": event["at_utc"] if event else None,
                           "published_by": event["actor"] if event else None})
        return sorted(result, key=lambda item: (item["prepared_at_utc"], item["id"]), reverse=True)

    def _activate(self, version_id: str, actor: str, expected_generation: int, action: str) -> dict:
        active = self._active_at(expected_generation)
        manifest = self._verify_bundle(version_id)
        # Validate the prior chain before extending it; corrupted history fails closed.
        self._events(active)
        event_id = uuid.uuid4().hex
        timestamp = _now()
        event = {"schema_version": 1, "event_id": event_id, "action": action,
                 "version_id": version_id, "actor": _actor(actor), "at_utc": timestamp,
                 "expected_active_generation": expected_generation,
                 "previous_event_id": active["event_id"] if active else None,
                 "previous_version": active["active_version"] if active else None,
                 "manifest_sha256": _sha(_json(manifest))}
        event_bytes = _json(event)
        self._create(f"logs/{event_id}.json", event_bytes, "application/json")
        if self.storage.get(f"logs/{event_id}.json").data != event_bytes:
            raise VersionIntegrityError("Audit event readback failed")
        pointer = {"schema_version": 1, "active_version": version_id,
                   "data_date": manifest["data_date"], "published_at_utc": timestamp,
                   "published_by": actor, "event_id": event_id, "action": action}
        try:
            generation = self.storage.put(self.ACTIVE_KEY, _json(pointer),
                                          if_generation_match=expected_generation,
                                          content_type="application/json")
        except Exception as error:
            # A network timeout may occur after GCS accepted the CAS. Recover by
            # checking the authoritative chain; never blindly repeat a publish.
            try:
                current = self.get_active()
                if any(item["event_id"] == event_id for item in self._events(current)):
                    return {**pointer, "_generation": current["_generation"],
                            "_outcome_recovered": True,
                            "_currently_active": current["event_id"] == event_id}
            except Exception:
                pass
            if isinstance(error, PreconditionFailed):
                raise
            raise PublicationUncertain("Pointer-write outcome unknown; inspect active/history before retrying") from error
        return {**pointer, "_generation": generation}

    def commit(self, preview_id: str, confirmation_token: str, actor: str,
               expected_active_generation: int) -> dict:
        manifest = self.get_manifest(preview_id)
        if manifest["expected_active_generation"] != expected_active_generation:
            raise ConfirmationError("Preview is bound to another active generation")
        if not isinstance(confirmation_token, str) or not hmac.compare_digest(
                confirmation_token, self._token("publish", preview_id, expected_active_generation)):
            raise ConfirmationError("Confirmation token does not match this preview")
        return self._activate(preview_id, actor, expected_active_generation, "publish")

    def preview_rollback(self, version_id: str, expected_active_generation: int) -> dict:
        active = self._active_at(expected_active_generation)
        if not any(event["version_id"] == version_id for event in self._events(active)):
            raise ConfirmationError("Rollback target has never been published")
        manifest = self._verify_bundle(version_id)
        return {"id": version_id, "data_date": manifest["data_date"],
                "expected_active_generation": expected_active_generation,
                "confirmation_token": self._token("rollback", version_id, expected_active_generation)}

    def rollback(self, version_id: str, actor: str, expected_active_generation: int,
                 confirmation_token: str) -> dict:
        preview = self.preview_rollback(version_id, expected_active_generation)
        if not isinstance(confirmation_token, str) or not hmac.compare_digest(
                confirmation_token, preview["confirmation_token"]):
            raise ConfirmationError("Confirmation token does not match rollback target/state")
        return self._activate(version_id, actor, expected_active_generation, "rollback")
