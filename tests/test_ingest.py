"""End-to-end local V1-A tests use only generated fake Excel bytes."""
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts import ingest_excel
from services.config import Settings
from services.storage import LocalStorageAdapter, PreconditionFailed
from services.validation import ValidationError
from services.versions import ConfirmationError, VersionManager
from test_validation import MAP_COLUMNS, MAP_ROWS, ROWS, workbook_bytes


class IngestionWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.settings = replace(Settings(), local_data_dir=self.root / "objects")
        self.manager = VersionManager(LocalStorageAdapter(self.settings.local_data_dir))
        self.inventory = self.root / "fake.xlsx"
        self.classification = self.root / "mapping.xlsx"
        self.inventory.write_bytes(workbook_bytes())
        self.classification.write_bytes(workbook_bytes(MAP_COLUMNS, MAP_ROWS))
        self.actor = "local-admin (mô phỏng)"

    def tearDown(self):
        self.temp.cleanup()

    def args(self, *args):
        return ingest_excel.parser().parse_args(args)

    def preview(self, classification=True):
        arguments = ["preview", "--file", str(self.inventory), "--data-date", "2026-10-13"]
        if classification:
            arguments.extend(["--classification", str(self.classification)])
        return ingest_excel.execute(self.args(*arguments), self.settings, self.manager, self.actor)

    def publish(self, preview):
        return ingest_excel.execute(self.args("publish", "--version", preview["version"], "--confirm", preview["confirmation_token"], "--expected-generation", str(preview["expected_generation"])), self.settings, self.manager, self.actor)

    def test_preview_stages_originals_and_does_not_publish(self):
        result = self.preview()
        self.assertFalse(result["published"])
        self.assertIsNone(self.manager.get_active())
        self.assertEqual(result["comparison"]["rows"]["after"], len(ROWS))
        self.assertEqual(result["comparison"]["units"]["after"], 2)
        stages = [key for key in self.manager.storage.list("staging/") if key.endswith("source.xlsx")]
        self.assertEqual(len(stages), 2)
        self.assertIn(self.inventory.read_bytes(), [self.manager.storage.get(key).data for key in stages])
        manifest = self.manager.get_manifest(result["version"])
        self.assertEqual(self.manager.storage.get(manifest["artifacts"]["source"]["key"]).data, self.inventory.read_bytes())

    def test_publish_requires_exact_token_generation_and_version(self):
        preview = self.preview()
        with self.assertRaises(ConfirmationError):
            self.manager.commit(preview["version"], "wrong-token", self.actor, 0)
        self.assertIsNone(self.manager.get_active())
        published = self.publish(preview)
        self.assertEqual(published["version"], preview["version"])
        self.assertGreater(published["generation"], 0)

    def test_two_previews_cannot_silently_overwrite(self):
        first, second = self.preview(), self.preview()
        self.publish(first)
        with self.assertRaises(PreconditionFailed):
            self.publish(second)
        self.assertEqual(self.manager.get_active()["active_version"], first["version"])

    def test_invalid_xlsx_is_retained_and_active_stays_unchanged(self):
        good = self.preview(); self.publish(good)
        invalid = b"fake corrupt XLSX ORIGINAL_BYTES"
        self.inventory.write_bytes(invalid)
        with self.assertRaises(ValidationError) as raised:
            self.preview()
        stage = self.manager.get_stage(raised.exception.report["stage_id"])
        self.assertEqual(self.manager.storage.get(stage["source_key"]).data, invalid)
        report = json.loads(self.manager.storage.get(f"staging/{stage['id']}/validation.json").data)
        self.assertFalse(report["valid"])
        self.assertEqual(self.manager.get_active()["active_version"], good["version"])

    def test_empty_workbook_is_retained_with_failed_report(self):
        self.inventory.write_bytes(b"")
        with self.assertRaises(ValidationError) as raised:
            self.preview()
        stage = self.manager.get_stage(raised.exception.report["stage_id"])
        self.assertEqual(self.manager.storage.get(stage["source_key"]).data, b"")
        self.assertEqual(raised.exception.code, "empty_file")
        self.assertIsNone(self.manager.get_active())

    def test_invalid_classification_retains_both_originals(self):
        self.classification.write_bytes(b"invalid mapping")
        with self.assertRaises(ValidationError) as raised:
            self.preview()
        stage_ids = [raised.exception.report["stage_id"], raised.exception.report["classification_stage_id"]]
        for stage_id in stage_ids:
            stage = self.manager.get_stage(stage_id)
            self.assertTrue(self.manager.storage.get(stage["source_key"]).data)
        self.assertIsNone(self.manager.get_active())

    def test_missing_initial_mapping_blocks_but_retains_inventory(self):
        with self.assertRaises(ValidationError) as raised:
            self.preview(classification=False)
        self.assertEqual(raised.exception.code, "missing_classification")
        self.assertIsNone(self.manager.get_active())
        self.assertIn("stage_id", raised.exception.report)

    def test_mapping_reuse_and_comparison_after_publish(self):
        first = self.preview(); self.publish(first)
        second = self.preview(classification=False)
        self.assertEqual(second["comparison"]["rows"]["delta"], 0)
        self.assertEqual(second["comparison"]["codes"]["delta"], 0)
        self.assertEqual(second["comparison"]["using_codes"]["delta"], 0)
        second_report = self.manager.read_validation(second["version"])
        first_report = self.manager.read_validation(first["version"])
        self.assertTrue(second_report["classification"]["reused_active_mapping"])
        self.assertEqual(second_report["classification_stage_id"], first_report["classification_stage_id"])
        manifest = self.manager.get_manifest(second["version"])
        self.assertIn("classification_source", manifest["artifacts"])
        self.assertEqual(self.manager.read_classification_source(second["version"]), self.classification.read_bytes())

    def test_rollback_is_preview_then_confirmation_independent_of_code(self):
        first = self.preview(); self.publish(first)
        second = self.preview(); self.publish(second)
        rollback = ingest_excel.execute(self.args("rollback", "--version", first["version"]), self.settings, self.manager, self.actor)
        self.assertEqual(self.manager.get_active()["active_version"], second["version"])
        result = ingest_excel.execute(self.args("rollback", "--version", first["version"], "--confirm", rollback["confirmation_token"], "--expected-generation", str(rollback["expected_generation"])), self.settings, self.manager, self.actor)
        self.assertEqual(result["version"], first["version"])
        self.assertEqual(self.manager.get_active()["active_version"], first["version"])

    def test_list_does_not_publish_prepared_versions(self):
        preview = self.preview()
        result = ingest_excel.execute(self.args("list"), self.settings, self.manager, self.actor)
        self.assertEqual(len(result["versions"]), 1)
        self.assertEqual(result["versions"][0]["version"], preview["version"])
        self.assertFalse(result["versions"][0]["published"])
        self.assertEqual(result["versions"][0]["metrics"]["rows"], len(ROWS))
        self.assertIsNone(self.manager.get_active())

    def test_no_actor_argument_and_cloud_identity_failure_precedes_storage(self):
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            self.args("list", "--actor", "admin@example.invalid")
        error = io.StringIO()
        with patch.object(ingest_excel.config.Settings, "from_env", return_value=self.settings), patch.object(ingest_excel.auth, "resolve_cli_actor", side_effect=PermissionError("TOKEN_MUST_NOT_LEAK")), patch.object(ingest_excel.config, "create_storage") as create, redirect_stderr(error):
            code = ingest_excel.main(["list"])
        self.assertEqual(code, 1)
        create.assert_not_called()
        self.assertNotIn("TOKEN_MUST_NOT_LEAK", error.getvalue())
        self.assertIn("error_id", error.getvalue())

    def test_main_workflow_outputs_json_and_preserves_source(self):
        before = self.inventory.read_bytes()
        output = io.StringIO()
        with patch.object(ingest_excel.config.Settings, "from_env", return_value=self.settings), redirect_stdout(output):
            code = ingest_excel.main(["preview", "--file", str(self.inventory), "--classification", str(self.classification), "--data-date", "2026-10-13"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output.getvalue())["operation"], "preview")
        self.assertEqual(self.inventory.read_bytes(), before)
        self.assertIsNone(self.manager.get_active())


if __name__ == "__main__":
    unittest.main()
