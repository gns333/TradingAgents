"""Stable request identities for task and report ownership."""

from __future__ import annotations

from dataclasses import dataclass


class IdentityRequired(ValueError):
    """Raised when a non-admin request has no verifiable identity."""


@dataclass(frozen=True)
class Principal:
    owner_key: str
    uid: str = ""
    email: str = ""
    is_admin: bool = False

    @classmethod
    def from_values(
        cls,
        uid: str | None = None,
        email: str | None = None,
        is_admin: bool = False,
    ) -> "Principal":
        clean_uid = str(uid or "").strip()
        clean_email = str(email or "").strip().lower()
        if is_admin:
            return cls("admin:local", clean_uid, clean_email, True)
        if clean_uid:
            return cls(f"uid:{clean_uid}", clean_uid, clean_email)
        if clean_email:
            return cls(f"email:{clean_email}", "", clean_email)
        raise IdentityRequired("A verified uid or email is required")
