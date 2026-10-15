import concurrent.futures
import io
import json
import tempfile
import threading
import unittest
from unittest.mock import patch

import pyarrow as pa
import pyarrow.parquet as pq

from services.storage import (GCSStorageAdapter, LocalStorageAdapter, ObjectData,
                              ObjectNotFound, PreconditionFailed)
from services.versions import (ConfirmationError, PublicationUncertain, VersionIntegrityError,
                               VersionManager)
try:
    from .test_storage import FakeGCSClient
except ImportError:
    from test_storage import FakeGCSClient


ACTOR = "admin@example.test"


def parquet(rows=3):
    output = io.BytesIO()
    table = pa.table({"Mã VTTB": [f"{i:04d}" for i in range(rows)],
                      "Serial": [f"S{i:04d}" for i in range(rows)],
                      "Số lượng": [1.0] * rows})
    pq.write_table(table, output)
    return output.getvalue()


class FaultStore:
    def __init__(self, delegate):
        self.delegate = delegate
        self.fail_prefix = None
        self.corrupt_key = None
        self.timeout_after_cas = False
        self.timeout_before_cas = False
        self.conflict_after_cas = False
        self.barrier = None
        self.writes = []

    def get(self, key):
        obj = self.delegate.get(key)
        if key == self.corrupt_key:
            return ObjectData(obj.data + b"corruption", obj.generation)
        return obj

    def put(self, key, data, **kwargs):
        self.writes.append(key)
        if self.fail_prefix and key.startswith(self.fail_prefix):
            raise OSError("simulated interrupted upload")
        if key == VersionManager.ACTIVE_KEY:
            if self.barrier:
                self.barrier.wait(timeout=10)
            if self.timeout_before_cas:
                raise TimeoutError("request outcome unknown")
        result = self.delegate.put(key, data, **kwargs)
        if key == VersionManager.ACTIVE_KEY and self.timeout_after_cas:
            raise TimeoutError("response lost after accepted CAS")
        if key == VersionManager.ACTIVE_KEY and self.conflict_after_cas:
            raise PreconditionFailed("retry after response loss observed a changed generation")
        return result

    def list(self, prefix=""):
        return self.delegate.list(prefix)


class PublicationContract:
    def prepare(self, expected=0, rows=3, actor=ACTOR, source=b"exact original workbook"):
        stage = self.manager.stage_upload(source, "inventory.xlsx", actor)
        return self.manager.prepare_version(stage, parquet(rows), {"mapped": ["type"]},
                                            {"valid": True, "row_count": rows, "errors": []},
                                            {"rows": rows, "unique_codes": rows},
                                            "2026-10-15", actor, expected)

    def publish(self, preview):
        return self.manager.commit(preview["id"], preview["confirmation_token"], ACTOR,
                                   preview["expected_active_generation"])

    def test_first_publish_pointer_last_and_lossless_source(self):
        original = b"original source bytes unchanged \x00\xff"
        preview = self.prepare(source=original)
        self.assertIsNone(self.manager.get_active())
        self.assertFalse(self.manager.list_versions()[0]["published"])
        pointer = self.publish(preview)
        self.assertEqual(pointer["active_version"], preview["id"])
        self.assertEqual(self.manager.get_active(), pointer)
        self.assertEqual(self.store.writes[-1], VersionManager.ACTIVE_KEY)
        manifest = self.manager.get_manifest(preview["id"])
        self.assertEqual(self.store.get(manifest["artifacts"]["source"]["key"]).data, original)
        self.assertEqual(pq.read_table(pa.BufferReader(self.manager.read_dataset(preview["id"]))).num_rows, 3)
        self.assertEqual(self.manager.read_mapping(preview["id"]), {"mapped": ["type"]})
        self.assertTrue(self.manager.list_versions()[0]["published"])

    def test_multiple_same_day_versions_and_independent_rollback(self):
        first = self.prepare(rows=2)
        pointer1 = self.publish(first)
        second = self.prepare(expected=pointer1["_generation"], rows=4)
        pointer2 = self.publish(second)
        self.assertNotEqual(first["id"], second["id"])
        rollback = self.manager.preview_rollback(first["id"], pointer2["_generation"])
        pointer3 = self.manager.rollback(first["id"], ACTOR, pointer2["_generation"],
                                         rollback["confirmation_token"])
        self.assertEqual(pointer3["active_version"], first["id"])
        versions = self.manager.list_versions()
        self.assertEqual(len(versions), 2)
        self.assertTrue(all(version["published"] for version in versions))
        self.assertEqual(sum(version["current"] for version in versions), 1)
        self.assertEqual(len(self.store.list("logs/")), 3)
        self.assertEqual(self.store.writes[-1], VersionManager.ACTIVE_KEY)

    def test_publication_rank_resolves_equal_timestamps_and_rollback(self):
        with patch("services.versions._now", return_value="2026-10-15T03:00:00Z"):
            first = self.prepare(rows=2)
            pointer1 = self.publish(first)
            second = self.prepare(expected=pointer1["_generation"], rows=4)
            prepared = self.prepare(expected=pointer1["_generation"], rows=1)
            pointer2 = self.publish(second)
            history = {item["id"]: item for item in self.manager.list_versions()}
            self.assertEqual(history[first["id"]]["publication_order"], 1)
            self.assertEqual(history[second["id"]]["publication_order"], 2)
            self.assertEqual(history[prepared["id"]]["publication_order"], 0)
            self.assertEqual(history[first["id"]]["published_at_utc"], history[second["id"]]["published_at_utc"])
            latest = max((item for item in history.values() if item["published"]),
                         key=lambda item: (item["data_date"], item["publication_order"]))
            self.assertEqual(latest["id"], second["id"])
            rollback = self.manager.preview_rollback(first["id"], pointer2["_generation"])
            self.manager.rollback(first["id"], ACTOR, pointer2["_generation"], rollback["confirmation_token"])
            after = {item["id"]: item for item in self.manager.list_versions()}
            self.assertEqual(after[first["id"]]["publication_order"], 3)
            self.assertEqual(after[second["id"]]["publication_order"], 2)
            self.assertEqual(after[prepared["id"]]["publication_order"], 0)
            latest = max((item for item in after.values() if item["published"]),
                         key=lambda item: (item["data_date"], item["publication_order"]))
            self.assertEqual(latest["id"], first["id"])

    def test_failed_validation_retained_without_snapshot_or_active_change(self):
        first = self.prepare()
        self.publish(first)
        before = self.manager.get_active()
        stage = self.manager.stage_upload(b"invalid source retained", "bad.xlsx", ACTOR)
        report = {"valid": False, "row_count": 0, "errors": ["schema"]}
        self.manager.record_stage_validation(stage["id"], report)
        with self.assertRaises(VersionIntegrityError):
            self.manager.prepare_version(stage, parquet(), {}, report, {}, "2026-10-15", ACTOR,
                                         before["_generation"])
        self.assertEqual(self.manager.get_active(), before)
        self.assertEqual(self.store.get(stage["source_key"]).data, b"invalid source retained")
        self.assertEqual(json.loads(self.store.get(f"staging/{stage['id']}/validation.json").data), report)
        self.assertEqual(len(self.manager.list_versions()), 1)
        with self.assertRaises(PreconditionFailed):
            self.manager.record_stage_validation(stage["id"], report)

    def test_empty_invalid_upload_can_be_retained_for_validation_audit(self):
        stage = self.manager.stage_upload(b"", "empty.xlsx", ACTOR)
        report = {"valid": False, "row_count": 0, "errors": ["empty"]}
        self.manager.record_stage_validation(stage["id"], report)
        self.assertEqual(self.store.get(stage["source_key"]).data, b"")
        self.assertEqual(stage["size_bytes"], 0)
        self.assertIsNone(self.manager.get_active())

    def test_token_binds_version_and_generation(self):
        first, second = self.prepare(), self.prepare()
        with self.assertRaises(ConfirmationError):
            self.manager.commit(second["id"], first["confirmation_token"], ACTOR, 0)
        with self.assertRaises(ConfirmationError):
            self.manager.commit(first["id"], first["confirmation_token"], ACTOR, 1)
        with self.assertRaises(ConfirmationError):
            self.manager.commit(first["id"], "wrong", ACTOR, 0)
        self.assertIsNone(self.manager.get_active())

    def test_stale_preview_cannot_overwrite_active(self):
        first, stale = self.prepare(), self.prepare()
        pointer = self.publish(first)
        with self.assertRaises(PreconditionFailed):
            self.publish(stale)
        self.assertEqual(self.manager.get_active(), pointer)
        history = {item["id"]: item for item in self.manager.list_versions()}
        self.assertTrue(history[first["id"]]["published"])
        self.assertFalse(history[stale["id"]]["published"])

    def test_concurrent_publishes_cas_single_winner_and_orphan_not_history(self):
        first, second = self.prepare(rows=2), self.prepare(rows=4)
        self.store.barrier = threading.Barrier(2)
        def commit(preview):
            try:
                return self.publish(preview)
            except PreconditionFailed:
                return None
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(commit, (first, second)))
        self.store.barrier = None
        self.assertEqual(sum(outcome is not None for outcome in outcomes), 1)
        self.assertEqual(len(self.store.list("logs/")), 2)
        history = self.manager.list_versions()
        self.assertEqual(sum(item["published"] for item in history), 1)
        self.assertEqual(sum(item["current"] for item in history), 1)
        orphan = next(item for item in history if not item["published"])
        with self.assertRaises(ConfirmationError):
            self.manager.preview_rollback(orphan["id"], self.manager.get_active()["_generation"])

    def test_snapshot_upload_failure_preserves_active(self):
        pointer = self.publish(self.prepare())
        self.store.fail_prefix = "versions/"
        with self.assertRaises(OSError):
            self.prepare(expected=pointer["_generation"])
        self.store.fail_prefix = None
        self.assertEqual(self.manager.get_active(), pointer)
        self.assertEqual(len(self.manager.list_versions()), 1)

    def test_readback_corruption_blocks_commit_and_retains_active(self):
        pointer = self.publish(self.prepare())
        preview = self.prepare(expected=pointer["_generation"])
        for name in ("source", "dataset", "mapping", "validation", "metrics"):
            self.store.corrupt_key = preview["artifacts"][name]["key"]
            with self.subTest(artifact=name), self.assertRaises(VersionIntegrityError):
                self.publish(preview)
            self.assertEqual(self.manager.get_active(), pointer)
        self.store.corrupt_key = None

    def test_bad_parquet_and_mismatched_report_row_count_never_publish(self):
        for data, count in ((b"invalid parquet", 3), (parquet(3), 9), (parquet(0), 0)):
            stage = self.manager.stage_upload(b"source", "file.xlsx", ACTOR)
            with self.subTest(count=count), self.assertRaises(VersionIntegrityError):
                self.manager.prepare_version(stage, data, {},
                    {"valid": True, "row_count": count, "errors": []}, {},
                    "2026-10-15", ACTOR, 0)
            self.assertIsNone(self.manager.get_active())

    def test_tampered_manifest_schema_blocks_readback_commit(self):
        preview = self.prepare()
        key = f"versions/{preview['id']}/manifest.json"
        manifest = json.loads(self.store.get(key).data)
        manifest["row_count"] = 99
        self.store.delegate.put(key, json.dumps(manifest).encode())
        tampered = self.manager.get_preview(preview["id"])
        with self.assertRaises(VersionIntegrityError):
            self.publish(tampered)
        self.assertIsNone(self.manager.get_active())

    def test_changed_staging_source_rejected(self):
        stage = self.manager.stage_upload(b"source", "file.xlsx", ACTOR)
        self.store.delegate.put(stage["source_key"], b"replaced outside manager")
        with self.assertRaises(VersionIntegrityError):
            self.manager.prepare_version(stage, parquet(), {},
                {"valid": True, "row_count": 3, "errors": []}, {}, "2026-10-15", ACTOR, 0)
        self.assertIsNone(self.manager.get_active())

    def test_classification_original_copied_immutably_and_verified_at_commit(self):
        original = b"exact classification XLSX bytes \x00\xff"
        classification_stage = self.manager.stage_upload(original, "mapping.xlsx", ACTOR)
        inventory_stage = self.manager.stage_upload(b"inventory original", "inventory.xlsx", ACTOR)
        preview = self.manager.prepare_version(inventory_stage, parquet(), {},
            {"valid": True, "row_count": 3, "errors": [],
             "classification_stage_id": classification_stage["id"]},
            {}, "2026-10-15", ACTOR, 0)
        self.assertEqual(self.manager.read_classification_source(preview["id"]), original)
        self.assertEqual(self.manager.read_source(preview["id"]), b"inventory original")
        source_key = preview["artifacts"]["classification_source"]["key"]
        self.assertEqual(source_key, f"raw/{preview['id']}/classification.xlsx")
        with self.assertRaises(PreconditionFailed):
            self.store.put(source_key, b"bad", if_generation_match=0)
        self.store.corrupt_key = source_key
        with self.assertRaises(VersionIntegrityError):
            self.publish(preview)
        self.assertIsNone(self.manager.get_active())
        self.store.corrupt_key = None
        self.publish(preview)
        rollback = self.manager.preview_rollback(preview["id"], self.manager.get_active()["_generation"])
        self.store.corrupt_key = source_key
        with self.assertRaises(VersionIntegrityError):
            self.manager.rollback(preview["id"], ACTOR, self.manager.get_active()["_generation"],
                                  rollback["confirmation_token"])

    def test_missing_or_modified_classification_stage_preserves_active(self):
        pointer = self.publish(self.prepare())
        stage = self.manager.stage_upload(b"inventory", "inventory.xlsx", ACTOR)
        missing_id = "20261015T030000Z_123456abcdef"
        with self.assertRaises(ObjectNotFound):
            self.manager.prepare_version(stage, parquet(), {},
                {"valid": True, "row_count": 3, "errors": [], "classification_stage_id": missing_id},
                {}, "2026-10-15", ACTOR, pointer["_generation"])
        classification_stage = self.manager.stage_upload(b"classification", "mapping.xlsx", ACTOR)
        self.store.delegate.put(classification_stage["source_key"], b"changed")
        with self.assertRaises(VersionIntegrityError):
            self.manager.prepare_version(stage, parquet(), {},
                {"valid": True, "row_count": 3, "errors": [],
                 "classification_stage_id": classification_stage["id"]},
                {}, "2026-10-15", ACTOR, pointer["_generation"])
        self.assertEqual(self.manager.get_active(), pointer)

    def test_rollback_requires_published_version_fresh_generation_and_exact_token(self):
        first = self.prepare()
        pointer1 = self.publish(first)
        second = self.prepare(expected=pointer1["_generation"])
        with self.assertRaises(ConfirmationError):
            self.manager.preview_rollback(second["id"], pointer1["_generation"])
        preview = self.manager.preview_rollback(first["id"], pointer1["_generation"])
        with self.assertRaises(ConfirmationError):
            self.manager.rollback(first["id"], ACTOR, pointer1["_generation"], "wrong")
        pointer2 = self.publish(second)
        with self.assertRaises(PreconditionFailed):
            self.manager.rollback(first["id"], ACTOR, pointer1["_generation"], preview["confirmation_token"])
        self.assertEqual(self.manager.get_active(), pointer2)

    def test_crash_after_pointer_accepted_recovered_without_second_write(self):
        preview = self.prepare()
        self.store.timeout_after_cas = True
        result = self.publish(preview)
        self.assertTrue(result["_outcome_recovered"])
        self.assertEqual(self.manager.get_active()["active_version"], preview["id"])
        self.assertTrue(self.manager.list_versions()[0]["published"])
        self.assertEqual(self.store.writes.count(VersionManager.ACTIVE_KEY), 1)

    def test_unknown_pointer_failure_does_not_mark_audit_intent_published(self):
        pointer = self.publish(self.prepare())
        preview = self.prepare(expected=pointer["_generation"])
        self.store.timeout_before_cas = True
        with self.assertRaises(PublicationUncertain):
            self.publish(preview)
        self.assertEqual(self.manager.get_active(), pointer)
        self.assertFalse(next(item for item in self.manager.list_versions() if item["id"] == preview["id"])["published"])

    def test_sdk_retry_conflict_after_accepted_pointer_recovers_committed_event(self):
        preview = self.prepare()
        self.store.conflict_after_cas = True
        pointer = self.publish(preview)
        self.assertTrue(pointer["_outcome_recovered"])
        self.assertEqual(self.manager.get_active()["active_version"], preview["id"])
        self.assertTrue(self.manager.list_versions()[0]["published"])

    def test_corrupt_active_pointer_never_falls_back_to_prepared_or_local(self):
        self.prepare()
        self.store.delegate.put(VersionManager.ACTIVE_KEY, b"{broken")
        with self.assertRaises(VersionIntegrityError):
            self.manager.get_active()
        with self.assertRaises(VersionIntegrityError):
            self.manager.list_versions()

    def test_published_manifest_cannot_be_silently_replaced_in_history(self):
        preview = self.prepare()
        self.publish(preview)
        key = f"versions/{preview['id']}/manifest.json"
        manifest = json.loads(self.store.get(key).data)
        manifest["note"] = "changed outside immutable writer"
        self.store.delegate.put(key, json.dumps(manifest).encode())
        with self.assertRaises(VersionIntegrityError):
            self.manager.list_versions()
        with self.assertRaises(VersionIntegrityError):
            self.manager.preview_rollback(preview["id"], self.manager.get_active()["_generation"])

    def test_invalid_ids_and_dates_rejected(self):
        for value in ("../../raw/x", "", "20261015"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.manager.get_manifest(value)
        stage = self.manager.stage_upload(b"source", "file.xlsx", ACTOR)
        with self.assertRaises(ValueError):
            self.manager.prepare_version(stage, parquet(), {},
                {"valid": True, "row_count": 3, "errors": []}, {}, "2026-02-30", ACTOR, 0)


class LocalPublicationTests(PublicationContract, unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = FaultStore(LocalStorageAdapter(self.temp.name))
        self.manager = VersionManager(self.store)

    def test_new_manager_sees_published_history_and_prepared_preview(self):
        preview = self.prepare()
        pointer = self.publish(preview)
        manager = VersionManager(LocalStorageAdapter(self.temp.name))
        self.assertEqual(manager.get_active(), pointer)
        self.assertEqual(manager.get_preview(preview["id"])["confirmation_token"], preview["confirmation_token"])
        self.assertTrue(manager.list_versions()[0]["published"])


class GCSPublicationTests(PublicationContract, unittest.TestCase):
    def setUp(self):
        self.client = FakeGCSClient()
        self.store = FaultStore(GCSStorageAdapter("bucket", "tests/publish", self.client))
        self.manager = VersionManager(self.store)


if __name__ == "__main__":
    unittest.main()
