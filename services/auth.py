"""Verified IAP identity for web and verified ADC identity for CLI mutations."""
from __future__ import annotations

from dataclasses import dataclass
import logging
import time

IAP_CERTS_URL = "https://www.gstatic.com/iap/verify/public_key"
IAP_ISSUER = "https://cloud.google.com/iap"
USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"
ADC_SCOPES = ("https://www.googleapis.com/auth/cloud-platform", "https://www.googleapis.com/auth/userinfo.email", "openid")
logger = logging.getLogger(__name__)


class AuthenticationError(PermissionError):
    pass


@dataclass(frozen=True)
class Identity:
    email: str
    subject: str
    is_admin: bool
    simulated: bool = False


class CachedCertificateRequest:
    """Cache Google's public certificates only; never cache or log user JWTs."""
    def __init__(self):
        self.cached = None
        self.expires = 0

    def __call__(self, url, method="GET", **kwargs):
        if url != IAP_CERTS_URL or method != "GET":
            raise AuthenticationError("Đích xác minh IAP không hợp lệ.")
        if self.cached is not None and time.monotonic() < self.expires:
            return self.cached
        from google.auth.transport.requests import Request
        response = Request()(url=url, method=method, timeout=15, **kwargs)
        if response.status == 200:
            self.cached = response
            self.expires = time.monotonic() + 300
        return response


_cert_request = CachedCertificateRequest()


def verify_iap_identity(headers, settings, verifier=None):
    if not settings.iap_audience:
        raise AuthenticationError("Thiếu cấu hình xác minh IAP.")
    assertion = next((v for k, v in headers.items() if k.casefold() == "x-goog-iap-jwt-assertion"), None)
    if not assertion or not isinstance(assertion, str):
        raise AuthenticationError("Yêu cầu phải đi qua IAP.")
    try:
        if verifier is None:
            from google.oauth2.id_token import verify_token
            claims = verify_token(assertion, _cert_request, audience=settings.iap_audience, certs_url=IAP_CERTS_URL)
        else:
            claims = verifier(assertion, settings.iap_audience)
        if claims.get("iss") != IAP_ISSUER or claims.get("aud") != settings.iap_audience:
            raise ValueError()
        if not isinstance(claims.get("exp"), (int, float)) or claims["exp"] <= time.time():
            raise ValueError()
        if not isinstance(claims.get("iat"), (int, float)) or claims["iat"] > time.time() + 30:
            raise ValueError()
        email, subject = claims.get("email", ""), claims.get("sub", "")
        if not isinstance(email, str) or "@" not in email or not isinstance(subject, str) or not subject:
            raise ValueError()
        email = email.casefold().strip()
        return Identity(email, subject, email in settings.admin_emails)
    except Exception:
        # No assertion/URL/credential values in error messages or stack traces.
        raise AuthenticationError("Không thể xác minh danh tính IAP. Vui lòng đăng nhập lại hoặc liên hệ quản trị.") from None


def resolve_cli_actor(settings):
    if not settings.cloud_mode:
        return "local-admin (mô phỏng)"
    if not settings.admin_emails:
        raise AuthenticationError("Chưa cấu hình ADMIN_EMAILS cho ingestion GCS.")
    try:
        import google.auth
        from google.auth.transport.requests import AuthorizedSession
        credentials, _ = google.auth.default(scopes=ADC_SCOPES)
        session = AuthorizedSession(credentials)
        try:
            response = session.get(USERINFO_URL, timeout=20)
            response.raise_for_status()
            claims = response.json()
        finally:
            session.close()
        email = claims.get("email", "").casefold().strip()
        if claims.get("email_verified") is not True or email not in settings.admin_emails:
            raise ValueError()
        return email
    except Exception:
        raise AuthenticationError("ADC chưa xác minh được Admin được phép thao tác. Đăng nhập ADC với scope email và Cloud Storage, kiểm tra ADMIN_EMAILS/IAM; không nhập token trong chat.") from None
