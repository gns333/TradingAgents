import base64
import json

import pytest
import requests

from tradingagents.web.identity import (
    CloudBaseAccessTokenVerifier,
    CloudBaseIdentityProvider,
    IdentityRequired,
    IdentityVerificationUnavailable,
    parse_cloudbase_context,
)


def _context(payload: dict) -> str:
    return base64.b64encode(json.dumps(payload).encode("utf-8")).decode("ascii")


class UserStore:
    def __init__(self, user):
        self.user = user

    def get_app_user(self, uid):
        if self.user and self.user["uid"] == uid:
            return self.user
        return None


class FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self.payload = payload
        self.ok = 200 <= status_code < 300

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if self.error:
            raise self.error
        return self.response


def test_cloudbase_context_decodes_uid_and_email():
    parsed = parse_cloudbase_context(
        _context({"uid": "cb-123", "email": "User@Example.com"})
    )

    assert parsed["uid"] == "cb-123"
    assert parsed["email"] == "User@Example.com"


def test_cloudbase_context_accepts_unpadded_base64url():
    encoded = base64.urlsafe_b64encode(
        json.dumps({"uid": "cb-123"}).encode("utf-8")
    ).decode("ascii").rstrip("=")

    parsed = parse_cloudbase_context(encoded)

    assert parsed["uid"] == "cb-123"


def test_cloudbase_provider_uses_database_role_not_browser_role():
    provider = CloudBaseIdentityProvider(
        UserStore(
            {
                "uid": "cb-123",
                "email": "user@example.com",
                "role": "user",
                "status": "active",
            }
        )
    )

    principal = provider.from_headers(
        {
            "x-cloudbase-context": _context(
                {
                    "uid": "cb-123",
                    "email": "user@example.com",
                    "role": "admin",
                }
            )
        }
    )

    assert principal.owner_key == "uid:cb-123"
    assert principal.role == "user"
    assert principal.is_admin is False


def test_cloudbase_provider_rejects_missing_unknown_and_disabled_users():
    with pytest.raises(IdentityRequired):
        CloudBaseIdentityProvider(UserStore(None)).from_headers({})

    with pytest.raises(PermissionError):
        CloudBaseIdentityProvider(UserStore(None)).from_headers(
            {"x-cloudbase-context": _context({"uid": "unknown"})}
        )

    with pytest.raises(PermissionError):
        CloudBaseIdentityProvider(
            UserStore(
                {
                    "uid": "blocked",
                    "email": "",
                    "role": "user",
                    "status": "disabled",
                }
            )
        ).from_headers(
            {"x-cloudbase-context": _context({"uid": "blocked"})}
        )


def test_cloudbase_context_rejects_invalid_base64_and_missing_uid():
    with pytest.raises(IdentityRequired):
        parse_cloudbase_context("not-base64")

    with pytest.raises(IdentityRequired):
        parse_cloudbase_context(_context({"email": "user@example.com"}))


def test_cloudbase_access_token_verifier_uses_official_profile_and_cache():
    session = FakeSession(
        FakeResponse(
            payload={
                "sub": "cb-123",
                "email": "User@Example.com",
                "status": "ACTIVE",
            }
        )
    )
    verifier = CloudBaseAccessTokenVerifier(
        "env-123",
        "ap-shanghai",
        session=session,
    )
    headers = {
        "authorization": "Bearer real-user-token",
        "x-cloudbase-context": _context({"uid": "forged-admin"}),
    }

    first = verifier.from_headers(headers)
    second = verifier.from_headers(headers)

    assert first == {"uid": "cb-123", "email": "user@example.com"}
    assert second == first
    assert len(session.calls) == 1
    assert session.calls[0][0].endswith("/auth/v1/user/me")
    assert session.calls[0][1]["headers"]["Authorization"] == (
        "Bearer real-user-token"
    )


def test_cloudbase_access_token_verifier_rejects_invalid_tokens():
    verifier = CloudBaseAccessTokenVerifier(
        "env-123",
        "ap-shanghai",
        session=FakeSession(FakeResponse(status_code=401, payload={})),
    )

    with pytest.raises(IdentityRequired):
        verifier.from_headers({"authorization": "Bearer invalid"})


def test_cloudbase_access_token_verifier_reports_upstream_failure():
    verifier = CloudBaseAccessTokenVerifier(
        "env-123",
        "ap-shanghai",
        session=FakeSession(error=requests.ConnectionError("offline")),
    )

    with pytest.raises(IdentityVerificationUnavailable):
        verifier.from_headers({"authorization": "Bearer user-token"})
