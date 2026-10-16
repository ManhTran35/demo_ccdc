"""Disposable materialized Drive snapshots; no ingestion/publish workflow.

Drive files are the durable source. This cache may disappear on reboot and is
rebuilt by revalidation. The current-cache pointer changes only after all bytes
are written. It is separate from VersionManager's administrator publications.
"""
from datetime import datetime, timezone
import hashlib
import json
import re

from services.storage import ObjectNotFound


def _json(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False).encode()


class DriveSnapshotCache:
    CURRENT = "drive-cache/current.json"

    def __init__(self, storage):
        self.storage = storage

    @staticmethod
    def _key(snapshot_id, name):
        if not re.fullmatch(r"[0-9a-f]{64}", snapshot_id):
            raise ValueError("Invalid Drive cache ID")
        return f"drive-cache/{snapshot_id}/{name}"

    def get_active(self):
        try:
            obj = self.storage.get(self.CURRENT)
        except ObjectNotFound:
            return None
        return dict(json.loads(obj.data), _generation=obj.generation)

    def materialize(self, snapshot_id, source_name, cutoff, content, classification, result, mapping):
        if not result.report.get("valid") or result.report.get("errors"):
            raise ValueError("Unvalidated data cannot enter the Drive cache")
        payloads = {"source.xlsx": content, "reference.xlsx": classification,
                    "dataset.parquet": result.parquet_bytes, "mapping.json": _json(mapping),
                    "validation.json": _json(result.report), "metrics.json": _json(result.metrics)}
        manifest = {"id": snapshot_id, "data_date": cutoff, "source_filename": source_name,
                    "row_count": result.report["row_count"], "note": "Nguồn Google Drive · bản chụp toàn bộ",
                    "cached_at_utc": datetime.now(timezone.utc).isoformat(),
                    "sha256": {name: hashlib.sha256(data).hexdigest() for name, data in payloads.items()}}
        for name, data in payloads.items():
            self.storage.put(self._key(snapshot_id, name), data)
        # The manifest is the completion marker, not a durable publication.
        self.storage.put(self._key(snapshot_id, "manifest.json"), _json(manifest))

    def activate(self, snapshot_id, expected_generation):
        manifest = self.get_manifest(snapshot_id)
        pointer = {"active_version": snapshot_id, "data_date": manifest["data_date"],
                   "activated_at_utc": datetime.now(timezone.utc).isoformat()}
        self.storage.put(self.CURRENT, _json(pointer), if_generation_match=expected_generation)

    def get_manifest(self, snapshot_id):
        return json.loads(self.storage.get(self._key(snapshot_id, "manifest.json")).data)

    def _read(self, snapshot_id, name):
        content = self.storage.get(self._key(snapshot_id, name)).data
        expected = self.get_manifest(snapshot_id)["sha256"][name]
        if hashlib.sha256(content).hexdigest() != expected:
            raise ValueError("Drive cache integrity check failed")
        return content

    def read_dataset(self, snapshot_id):
        return self._read(snapshot_id, "dataset.parquet")

    def read_source(self, snapshot_id):
        return self._read(snapshot_id, "source.xlsx")

    def read_classification_source(self, snapshot_id):
        return self._read(snapshot_id, "reference.xlsx")

    def read_mapping(self, snapshot_id):
        return json.loads(self._read(snapshot_id, "mapping.json"))

    def read_validation(self, snapshot_id):
        return json.loads(self._read(snapshot_id, "validation.json"))

    def read_metrics(self, snapshot_id):
        return json.loads(self._read(snapshot_id, "metrics.json"))

    def list_versions(self):
        active = self.get_active()
        return [dict(json.loads(self.storage.get(key).data), current=bool(active and key == self._key(active["active_version"], "manifest.json")))
                for key in self.storage.list("drive-cache/") if key.endswith("/manifest.json")]
