"""Stable request identities for task and report ownership."""

from __future__ import annotations

import base64
import hashlib
import json
import threading
import time
from collections import OrderedDict
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

import requests

from .runtime import WebRuntimeConfig
from .store import ApplicationStore


class IdentityRequired(ValueError):
    """Raised when a non-admin request has no verifiable identity."""


class IdentityVerificationUnavailable(RuntimeError):
    """Raised when CloudBase cannot currently verify an access token."""


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


class CloudBaseAccessTokenVerifier:
    """Resolve a CloudBase user from a bearer token using the Auth API."""

    def __init__(
        self,
        env_id: str,
        region: str,
        *,
        session: requests.Session | None = None,
        cache_ttl: float = 60.0,
        cache_size: int = 512,
    ):
        self.url = (
            f"https://{env_id}.{region}.tcb-api.tencentcloudapi.com"
            "/auth/v1/user/me"
        )
        self.session = session or requests.Session()
        self.cache_ttl = cache_ttl
        self.cache_size = cache_size
        self._cache: OrderedDict[str, tuple[float, dict[str, object]]] = (
            OrderedDict()
        )
        self._lock = threading.Lock()

    @staticmethod
    def _bearer_token(headers: Mapping[str, str]) -> str:
        authorization = str(headers.get("authorization") or "").strip()
        scheme, separator, token = authorization.partition(" ")
        if not separator or scheme.lower() != "bearer" or not token.strip():
            raise IdentityRequired("CloudBase access token is required")
        return token.strip()

    def _cached(self, cache_key: str) -> dict[str, object] | None:
        now = time.monotonic()
        with self._lock:
            cached = self._cache.get(cache_key)
            if cached is None:
                return None
            expires_at, context = cached
            if expires_at <= now:
                self._cache.pop(cache_key, None)
                return None
            self._cache.move_to_end(cache_key)
            return dict(context)

    def _remember(self, cache_key: str, context: dict[str, object]) -> None:
        with self._lock:
            self._cache[cache_key] = (
                time.monotonic() + self.cache_ttl,
                dict(context),
            )
            self._cache.move_to_end(cache_key)
            while len(self._cache) > self.cache_size:
                self._cache.popitem(last=False)

    def from_headers(self, headers: Mapping[str, str]) -> dict[str, object]:
        token = self._bearer_token(headers)
        cache_key = hashlib.sha256(token.encode("utf-8")).hexdigest()
        cached = self._cached(cache_key)
        if cached is not None:
            return cached

        try:
            response = self.session.get(
                self.url,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/json",
                },
                timeout=(3.05, 5),
            )
        except requests.RequestException as exc:
            raise IdentityVerificationUnavailable(
                "CloudBase identity service is unavailable"
            ) from exc

        if response.status_code in {401, 403}:
            raise IdentityRequired("CloudBase access token is invalid")
        if not response.ok:
            raise IdentityVerificationUnavailable(
                "CloudBase identity service returned an error"
            )
        try:
            profile = response.json()
        except ValueError as exc:
            raise IdentityVerificationUnavailable(
                "CloudBase identity service returned invalid JSON"
            ) from exc
        if not isinstance(profile, dict):
            raise IdentityVerificationUnavailable(
                "CloudBase identity service returned an invalid profile"
            )

        uid = str(profile.get("sub") or profile.get("user_id") or "").strip()
        if not uid:
            raise IdentityRequired("CloudBase identity profile has no uid")
        if str(profile.get("status") or "").upper() == "BLOCKED":
            raise IdentityRequired("CloudBase user is blocked")
        context: dict[str, object] = {
            "uid": uid,
            "email": str(profile.get("email") or "").strip().lower(),
        }
        self._remember(cache_key, context)
        return context


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
    def __init__(
        self,
        store: ApplicationStore,
        verifier: CloudBaseAccessTokenVerifier | None = None,
    ):
        self.store = store
        self.verifier = verifier

    def verified_context(
        self,
        headers: Mapping[str, str],
    ) -> dict[str, object]:
        if self.verifier is not None:
            return self.verifier.from_headers(headers)
        return parse_cloudbase_context(headers.get("x-cloudbase-context"))

    def from_headers(
        self,
        headers: Mapping[str, str],
        *,
        access_email: str | None = None,
        is_admin: bool = False,
    ) -> Principal:
        del access_email, is_admin
        context = self.verified_context(headers)
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
        return CloudBaseIdentityProvider(
            store,
            CloudBaseAccessTokenVerifier(
                config.cloudbase_env_id,
                config.cloudbase_region,
            ),
        )
    return LocalIdentityProvider()
