"""Stable request identities for task and report ownership."""

from __future__ import annotations

import base64
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from .runtime import WebRuntimeConfig
from .store import ApplicationStore


class IdentityRequired(ValueError):
    """Raised when a non-admin request has no verifiable identity."""


@dataclass(frozen=True)
class Principal:
    owner_key: str
    uid: str = ""
    email: str = ""
    role: str = "user"
    is_admin: bool = False

    @classmethod
    def from_values(
        cls,
        uid: str | None = None,
        email: str | None = None,
        is_admin: bool = False,
    ) -> Principal:
        clean_uid = str(uid or "").strip()
        clean_email = str(email or "").strip().lower()
        if is_admin:
            return cls(
                owner_key="admin:local",
                uid=clean_uid,
                email=clean_email,
                role="admin",
                is_admin=True,
            )
        if clean_uid:
            return cls(
                owner_key=f"uid:{clean_uid}",
                uid=clean_uid,
                email=clean_email,
            )
        if clean_email:
            return cls(
                owner_key=f"email:{clean_email}",
                email=clean_email,
            )
        raise IdentityRequired("A verified uid or email is required")


def parse_cloudbase_context(value: str | None) -> dict[str, object]:
    if not value:
        raise IdentityRequired("CloudBase identity context is required")
    try:
        encoded = value.strip()
        encoded += "=" * (-len(encoded) % 4)
        decoded = base64.b64decode(encoded, altchars=b"-_", validate=True)
        payload = json.loads(decoded.decode("utf-8"))
    except (ValueError, UnicodeError, json.JSONDecodeError) as exc:
        raise IdentityRequired("CloudBase identity context is invalid") from exc
    if not isinstance(payload, dict) or not str(payload.get("uid") or "").strip():
        raise IdentityRequired("CloudBase identity context has no uid")
    return payload


class IdentityProvider(Protocol):
    def from_headers(
        self,
        headers: Mapping[str, str],
        *,
        access_email: str | None = None,
        is_admin: bool = False,
    ) -> Principal: ...


class LocalIdentityProvider:
    def from_headers(
        self,
        headers: Mapping[str, str],
        *,
        access_email: str | None = None,
        is_admin: bool = False,
    ) -> Principal:
        return Principal.from_values(
            uid=(
                headers.get("x-cloudbase-uid")
                or headers.get("x-user-uid")
            ),
            email=(
                headers.get("x-cloudbase-email")
                or headers.get("x-user-email")
                or access_email
            ),
            is_admin=is_admin,
        )


class CloudBaseIdentityProvider:
    def __init__(self, store: ApplicationStore):
        self.store = store

    def from_headers(
        self,
        headers: Mapping[str, str],
        *,
        access_email: str | None = None,
        is_admin: bool = False,
    ) -> Principal:
        del access_email, is_admin
        context = parse_cloudbase_context(headers.get("x-cloudbase-context"))
        uid = str(context["uid"]).strip()
        user = self.store.get_app_user(uid)
        if user is None or user["status"] != "active":
            raise PermissionError("CloudBase user is not allowed")
        role = str(user["role"])
        return Principal(
            owner_key=f"uid:{uid}",
            uid=uid,
            email=str(user.get("email") or "").strip().lower(),
            role=role,
            is_admin=role == "admin",
        )


def create_identity_provider(
    config: WebRuntimeConfig,
    store: ApplicationStore,
) -> IdentityProvider:
    if config.mode == "cloudbase":
        return CloudBaseIdentityProvider(store)
    return LocalIdentityProvider()
