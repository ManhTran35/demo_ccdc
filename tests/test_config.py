"""Cloud configuration must fail closed rather than silently using local files."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from services.config import ConfigurationError, Settings, create_storage


def cloud_env(**updates):
    values = {
        "APP_ENV": "production", "STORAGE_BACKEND": "gcs",
        "GCP_PROJECT_ID": "demo-project", "GCS_BUCKET": "demo-ccdc-data",
        "IAP_AUDIENCE": "/projects/123/global/backendServices/456",
        "ADMIN_EMAILS": "admin@example.com",
    }
    values.update(updates)
    return values


class ConfigurationTests(unittest.TestCase):
    def test_community_demo_is_read_only_and_ignores_real_local_directory(self):
        settings = Settings.from_env({"APP_ENV": "community-demo", "LOCAL_DATA_DIR": "/real-data"})
        self.assertTrue(settings.community_demo)
        self.assertTrue(settings.read_only)
        self.assertFalse(settings.cloud_mode)
        self.assertEqual(settings.storage_backend, "local")
        self.assertNotEqual(settings.local_data_dir, Path("/real-data"))
        self.assertIn("ccdc-community-demo-", settings.local_data_dir.name)

    def test_community_demo_cannot_replace_authenticated_gcs_or_cloud_run(self):
        for extra in ({"STORAGE_BACKEND": "gcs"}, {"K_SERVICE": "dashboard"}):
            with self.subTest(extra=extra), self.assertRaises(ConfigurationError):
                Settings.from_env({"APP_ENV": "community-demo", **extra})

    def test_empty_environment_is_local_and_explicitly_not_cloud(self):
        settings = Settings.from_env({})
        self.assertEqual(settings.app_env, "local")
        self.assertEqual(settings.storage_backend, "local")
        self.assertFalse(settings.cloud_mode)

    def test_valid_cloud_configuration_uses_gcs(self):
        settings = Settings.from_env(cloud_env(GCS_PREFIX="ccdc/staging"))
        self.assertTrue(settings.cloud_mode)
        self.assertEqual(settings.project_id, "demo-project")
        self.assertEqual(settings.bucket, "demo-ccdc-data")
        self.assertEqual(settings.prefix, "ccdc/staging")

    def test_cloud_run_defaults_to_production_gcs(self):
        values = cloud_env(K_SERVICE="dashboard-ccdc")
        del values["APP_ENV"]
        del values["STORAGE_BACKEND"]
        settings = Settings.from_env(values)
        self.assertEqual(settings.app_env, "production")
        self.assertTrue(settings.cloud_mode)

    def test_cloud_run_cannot_enable_local_simulation_with_either_backend(self):
        for backend in ("local", "gcs"):
            with self.subTest(backend=backend):
                with self.assertRaises(ConfigurationError):
                    Settings.from_env(cloud_env(
                        K_SERVICE="dashboard-ccdc", APP_ENV="local", STORAGE_BACKEND=backend
                    ))

    def test_staging_and_production_cannot_use_local_backend(self):
        for app_env in ("staging", "production"):
            with self.subTest(app_env=app_env):
                with self.assertRaises(ConfigurationError):
                    Settings.from_env(cloud_env(APP_ENV=app_env, STORAGE_BACKEND="local"))

    def test_cloud_requires_project_bucket_and_iap_audience(self):
        for name in ("GCP_PROJECT_ID", "GCS_BUCKET", "IAP_AUDIENCE"):
            for value in ("", "   "):
                with self.subTest(name=name, value=value):
                    with self.assertRaises(ConfigurationError):
                        Settings.from_env(cloud_env(**{name: value}))

    def test_unknown_runtime_and_backend_fail_closed(self):
        for updates in ({"APP_ENV": "dev"}, {"STORAGE_BACKEND": "filesystem"}):
            with self.subTest(updates=updates):
                with self.assertRaises(ConfigurationError):
                    Settings.from_env(cloud_env(**updates))

    def test_bucket_and_prefix_reject_ambiguous_or_traversal_paths(self):
        for bucket in ("", "UPPERCASE", "gs://demo-bucket", "a", "-demo-bucket"):
            with self.subTest(bucket=bucket):
                with self.assertRaises(ConfigurationError):
                    Settings.from_env(cloud_env(GCS_BUCKET=bucket))
        for prefix in ("../other", "valid/../other", "a//b", "a\\b", "a?key=value"):
            with self.subTest(prefix=prefix):
                with self.assertRaises(ConfigurationError):
                    Settings.from_env(cloud_env(GCS_PREFIX=prefix))

    def test_admin_allowlist_is_normalized_and_deduplicated(self):
        settings = Settings.from_env(cloud_env(
            ADMIN_EMAILS=" Viewer@Example.com, ADMIN@example.com,admin@example.com, "
        ))
        self.assertEqual(settings.admin_emails, ("admin@example.com", "viewer@example.com"))
        for emails in ("not-an-email", "admin@", "admin@example.com\nattacker@example.com"):
            with self.subTest(emails=emails):
                with self.assertRaises(ConfigurationError):
                    Settings.from_env(cloud_env(ADMIN_EMAILS=emails))

    def test_resource_limits_require_positive_integers(self):
        for name in ("MAX_UPLOAD_MB", "MAX_UNCOMPRESSED_MB", "MAX_ROWS", "MAX_COLUMNS"):
            for value in ("0", "-1", "1.5", "infinity", ""):
                with self.subTest(name=name, value=value):
                    with self.assertRaises(ConfigurationError):
                        Settings.from_env({name: value})
        settings = Settings.from_env({"MAX_ROWS": "123", "MAX_UPLOAD_MB": "2"})
        self.assertEqual(settings.max_rows, 123)
        self.assertEqual(settings.max_upload_mb, 2)

    def test_warning_thresholds_require_finite_proportions(self):
        for value in ('[]', '"text"', '{"missing": -0.1}', '{"missing": 1.1}',
                      '{"missing": true}', '{"missing": "0.2"}', '{"missing": NaN}',
                      '{"missing": Infinity}', "invalid-json"):
            with self.subTest(value=value):
                with self.assertRaises(ConfigurationError):
                    Settings.from_env({"VALIDATION_WARNING_LIMITS": value})
        self.assertEqual(
            Settings.from_env({"VALIDATION_WARNING_LIMITS": '{"missing": 0.2}'}).warning_limits,
            {"missing": 0.2},
        )

    def test_cloud_client_failure_does_not_construct_local_storage(self):
        settings = Settings.from_env(cloud_env())
        with patch("google.cloud.storage.Client", side_effect=RuntimeError("no ADC")), \
                patch("services.storage.LocalStorageAdapter") as local:
            with self.assertRaises(RuntimeError):
                create_storage(settings)
        local.assert_not_called()

    def test_explicit_local_storage_uses_configured_data_directory_without_cloud_client(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = Settings.from_env({"LOCAL_DATA_DIR": directory})
            with patch("google.cloud.storage.Client") as cloud:
                store = create_storage(settings)
            self.assertEqual(store.root, Path(directory).resolve())
            cloud.assert_not_called()


if __name__ == "__main__":
    unittest.main()
