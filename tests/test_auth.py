"""Authentication regression tests use real signed JWTs and no network."""
import json
import time
import unittest
from unittest.mock import Mock, patch

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
import jwt

from services import auth
from services.config import Settings


AUDIENCE = "/projects/123456789/global/backendServices/987654321"
ADMIN = "admin@example.com"
VIEWER = "viewer@example.com"


class IAPTests(unittest.TestCase):
    """Exercise Google's real ES256 signature verification, not a claims stub."""

    @classmethod
    def setUpClass(cls):
        cls.key = ec.generate_private_key(ec.SECP256R1())
        cls.other_key = ec.generate_private_key(ec.SECP256R1())
        cls.public_key = cls.key.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        ).decode("ascii")

    def setUp(self):
        self.settings = Settings(
            app_env="production", storage_backend="gcs",
            project_id="demo-project", bucket="demo-bucket",
            admin_emails=(ADMIN,), iap_audience=AUDIENCE,
        )
        self.response = Mock(status=200)
        self.response.data = json.dumps({"trusted-key": self.public_key}).encode()
        self.request = Mock(return_value=self.response)
        self.request_patch = patch(
            "google.auth.transport.requests.Request", return_value=self.request
        )
        self.request_patch.start()
        self.addCleanup(self.request_patch.stop)
        self.cache_patch = patch.object(auth, "_cert_request", auth.CachedCertificateRequest())
        self.cache_patch.start()
        self.addCleanup(self.cache_patch.stop)

    def token(self, *, key=None, **overrides):
        now = int(time.time())
        claims = {
            "iss": auth.IAP_ISSUER, "aud": AUDIENCE,
            "iat": now - 10, "exp": now + 300,
            "email": VIEWER, "sub": "accounts.google.com:verified-subject",
        }
        claims.update(overrides)
        return jwt.encode(
            claims, key or self.key, algorithm="ES256", headers={"kid": "trusted-key"}
        )

    def verify(self, token, **headers):
        return auth.verify_iap_identity(
            {"X-Goog-IAP-JWT-Assertion": token, **headers}, self.settings
        )

    def test_real_es256_token_yields_verified_viewer(self):
        identity = self.verify(self.token())
        self.assertEqual(identity.email, VIEWER)
        self.assertEqual(identity.subject, "accounts.google.com:verified-subject")
        self.assertFalse(identity.is_admin)
        self.assertFalse(identity.simulated)
        self.request.assert_called_once_with(
            url=auth.IAP_CERTS_URL, method="GET", timeout=15
        )

    def test_admin_status_comes_only_from_allowlist(self):
        identity = self.verify(self.token(email="ADMIN@EXAMPLE.COM"))
        self.assertTrue(identity.is_admin)
        self.assertEqual(identity.email, ADMIN)
        stranger = self.verify(self.token(email="admin@attacker.example"))
        self.assertFalse(stranger.is_admin)

    def test_email_role_and_subject_headers_cannot_elevate_viewer(self):
        identity = self.verify(
            self.token(),
            **{
                "X-Goog-Authenticated-User-Email": "accounts.google.com:" + ADMIN,
                "X-Goog-Authenticated-User-ID": "accounts.google.com:admin",
                "X-User-Email": ADMIN,
                "X-Role": "admin",
            },
        )
        self.assertEqual(identity.email, VIEWER)
        self.assertFalse(identity.is_admin)
        self.assertFalse(identity.simulated)

    def test_header_lookup_is_case_insensitive(self):
        identity = auth.verify_iap_identity(
            {"x-GOoG-iap-jwt-ASsertion": self.token()}, self.settings
        )
        self.assertEqual(identity.email, VIEWER)

    def test_wrong_signature_audience_issuer_and_time_are_rejected(self):
        now = int(time.time())
        tokens = {
            "wrong-signature": self.token(key=self.other_key),
            "wrong-audience": self.token(aud=AUDIENCE + "0"),
            "wrong-issuer": self.token(iss="https://attacker.example"),
            "expired": self.token(iat=now - 600, exp=now - 300),
            "future-issued": self.token(iat=now + 600, exp=now + 900),
        }
        for reason, token in tokens.items():
            with self.subTest(reason=reason):
                with self.assertRaises(auth.AuthenticationError) as error:
                    self.verify(token)
                self.assertNotIn(token, str(error.exception))

    def test_missing_assertion_and_spoofed_email_alone_are_rejected(self):
        for headers in ({}, {"X-Goog-Authenticated-User-Email": ADMIN},
                        {"X-Goog-IAP-JWT-Assertion": ""},
                        {"X-Goog-IAP-JWT-Assertion": 42}):
            with self.subTest(headers=headers):
                with self.assertRaises(auth.AuthenticationError):
                    auth.verify_iap_identity(headers, self.settings)
        self.request.assert_not_called()

    def test_invalid_token_identity_claims_are_rejected(self):
        for claims in ({"email": ""}, {"email": 123}, {"sub": ""}, {"sub": None}):
            with self.subTest(claims=claims):
                with self.assertRaises(auth.AuthenticationError):
                    self.verify(self.token(**claims))

    def test_missing_iap_configuration_fails_closed(self):
        with self.assertRaises(auth.AuthenticationError):
            auth.verify_iap_identity(
                {"X-Goog-IAP-JWT-Assertion": self.token()}, Settings()
            )
        self.request.assert_not_called()

    def test_certificate_failure_cannot_enable_fallback_identity(self):
        self.response.status = 503
        self.response.data = b"backend unavailable"
        with self.assertRaises(auth.AuthenticationError):
            self.verify(self.token(), **{"X-Goog-Authenticated-User-Email": ADMIN})

    def test_certificate_cache_reuses_public_keys_for_distinct_tokens(self):
        first = self.verify(self.token(email=VIEWER))
        second = self.verify(self.token(email=ADMIN))
        self.assertNotEqual(first.email, second.email)
        self.request.assert_called_once()
        self.assertIs(auth._cert_request.cached, self.response)

    def test_certificate_transport_refuses_unexpected_destinations(self):
        cache = auth.CachedCertificateRequest()
        for url, method in (("https://attacker.example", "GET"),
                            (auth.IAP_CERTS_URL, "POST")):
            with self.subTest(url=url, method=method):
                with self.assertRaises(auth.AuthenticationError):
                    cache(url, method=method)
        self.request.assert_not_called()


class CLIIdentityTests(unittest.TestCase):
    def setUp(self):
        self.settings = Settings(
            app_env="production", storage_backend="gcs",
            admin_emails=(ADMIN,), iap_audience=AUDIENCE,
        )
        self.credentials = object()
        self.response = Mock()
        self.response.json.return_value = {"email": ADMIN, "email_verified": True}
        self.session = Mock()
        self.session.get.return_value = self.response
        self.default_patch = patch(
            "google.auth.default", return_value=(self.credentials, "adc-project")
        )
        self.default = self.default_patch.start()
        self.addCleanup(self.default_patch.stop)
        self.session_patch = patch(
            "google.auth.transport.requests.AuthorizedSession", return_value=self.session
        )
        self.authorized_session = self.session_patch.start()
        self.addCleanup(self.session_patch.stop)

    def test_cloud_actor_is_verified_adc_userinfo_allowlisted_email(self):
        self.response.json.return_value = {"email": "ADMIN@EXAMPLE.COM", "email_verified": True}
        with patch.dict("os.environ", {"USER": "spoofed", "ACTOR": "spoofed@example.com"}):
            self.assertEqual(auth.resolve_cli_actor(self.settings), ADMIN)
        self.default.assert_called_once_with(scopes=auth.ADC_SCOPES)
        self.authorized_session.assert_called_once_with(self.credentials)
        self.session.get.assert_called_once_with(auth.USERINFO_URL, timeout=20)
        self.response.raise_for_status.assert_called_once_with()
        self.session.close.assert_called_once_with()

    def test_cloud_requires_verified_email_and_exact_allowlist_membership(self):
        for claims in (
            {"email": ADMIN, "email_verified": False},
            {"email": ADMIN, "email_verified": "true"},
            {"email": ADMIN},
            {"email": VIEWER, "email_verified": True},
            {"email": "admin@example.com.attacker.example", "email_verified": True},
            {"email": None, "email_verified": True},
            {},
        ):
            with self.subTest(claims=claims):
                self.response.json.return_value = claims
                with self.assertRaises(auth.AuthenticationError):
                    auth.resolve_cli_actor(self.settings)
        self.assertEqual(self.session.close.call_count, 7)

    def test_cloud_without_allowlist_has_no_simulated_admin(self):
        settings = Settings(storage_backend="gcs", admin_emails=())
        with self.assertRaises(auth.AuthenticationError):
            auth.resolve_cli_actor(settings)
        self.default.assert_not_called()

    def test_failed_adc_or_userinfo_never_falls_back_to_local_identity(self):
        for failed_step in ("credentials", "http", "json"):
            with self.subTest(failed_step=failed_step):
                self.default.side_effect = None
                self.response.raise_for_status.side_effect = None
                self.response.json.side_effect = None
                if failed_step == "credentials":
                    self.default.side_effect = RuntimeError("credential-secret-value")
                elif failed_step == "http":
                    self.response.raise_for_status.side_effect = RuntimeError("credential-secret-value")
                else:
                    self.response.json.side_effect = ValueError("credential-secret-value")
                with self.assertRaises(auth.AuthenticationError) as error:
                    auth.resolve_cli_actor(self.settings)
                self.assertNotIn("credential-secret-value", str(error.exception))

    def test_local_cli_actor_is_explicitly_simulated(self):
        self.assertIn("mô phỏng", auth.resolve_cli_actor(Settings()))
        self.default.assert_not_called()
        self.authorized_session.assert_not_called()


if __name__ == "__main__":
    unittest.main()
