from pathlib import Path

from tradingagents.web.admin_store import AdminStore, mask_secret


def test_model_api_key_is_encrypted_and_masked(tmp_path: Path):
    store = AdminStore(tmp_path / "admin.sqlite3")

    item = store.save_model_config(
        {
            "provider": "deepseek",
            "display_name": "DeepSeek",
            "quick_model": "deepseek-v4-flash",
            "deep_model": "deepseek-v4-pro",
            "api_key": "sk-live-secret",
            "is_default": True,
        }
    )

    assert item["api_key_masked"] == mask_secret("sk-live-secret")
    assert "sk-live-secret" not in str(item)

    runtime = store.get_default_runtime_model()
    assert runtime is not None
    assert runtime.provider == "deepseek"
    assert runtime.api_key == "sk-live-secret"


def test_admin_password_and_session_are_database_backed(tmp_path: Path):
    store = AdminStore(tmp_path / "admin.sqlite3")

    assert not store.admin_password_is_configured()
    store.set_admin_password("correct-horse")

    assert store.admin_password_is_configured()
    assert store.verify_admin_password("correct-horse")
    assert not store.verify_admin_password("wrong-password")

    token = store.create_admin_session()
    assert store.verify_admin_session(token)
    assert not store.verify_admin_session("not-the-token")


def test_whitelist_allows_everyone_until_configured(tmp_path: Path):
    store = AdminStore(tmp_path / "admin.sqlite3")

    assert store.is_identity_allowed(email="anyone@example.com")

    store.upsert_whitelist({"email": "a@example.com", "status": "active"})
    store.upsert_whitelist({"email": "b@example.com", "status": "blocked"})

    assert store.is_identity_allowed(email="a@example.com")
    assert not store.is_identity_allowed(email="b@example.com")
    assert not store.is_identity_allowed(email="c@example.com")
