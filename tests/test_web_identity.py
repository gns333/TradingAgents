import base64
import json

import pytest

from tradingagents.web.identity import (
    CloudBaseIdentityProvider,
    IdentityRequired,
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


def test_cloudbase_context_decodes_uid_and_email():
    parsed = parse_cloudbase_context(
        _context({"uid": "cb-123", "email": "User@Example.com"})
    )

    assert parsed["uid"] == "cb-123"
    assert parsed["email"] == "User@Example.com"


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
