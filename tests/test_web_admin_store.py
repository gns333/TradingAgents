from pathlib import Path

import pytest

from tradingagents.web.admin_store import AdminStore, mask_secret
from tradingagents.web.events import AnalysisEvent
from tradingagents.web.identity import Principal
from tradingagents.web.store import QueueLimitReached, TaskSubmissionPaused


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


def test_whitelist_is_required_after_admin_password_is_configured(tmp_path: Path):
    store = AdminStore(tmp_path / "admin.sqlite3")

    assert store.is_identity_allowed(email="anyone@example.com")

    store.set_admin_password("correct-horse")
    assert not store.is_identity_allowed(email="anyone@example.com")

    store.upsert_whitelist({"email": "a@example.com", "status": "active"})
    store.upsert_whitelist({"email": "b@example.com", "status": "blocked"})

    assert store.is_identity_allowed(email="a@example.com")
    assert not store.is_identity_allowed(email="b@example.com")
    assert not store.is_identity_allowed(email="c@example.com")


def test_principal_prefers_uid_and_normalizes_email():
    principal = Principal.from_values(uid=" cb-123 ", email="User@Example.com")

    assert principal.owner_key == "uid:cb-123"
    assert principal.uid == "cb-123"
    assert principal.email == "user@example.com"


def test_only_one_active_run_is_allowed_per_owner(tmp_path: Path):
    from tradingagents.web.admin_store import ActiveRunExists

    store = AdminStore(tmp_path / "admin.sqlite3")
    first = store.create_analysis_run(
        {
            "owner_key": "uid:u1",
            "ticker": "600519.SH",
            "trade_date": "2026-07-14",
            "analysts": ["market"],
        }
    )

    with pytest.raises(ActiveRunExists) as exc_info:
        store.create_analysis_run(
            {
                "owner_key": "uid:u1",
                "ticker": "000001.SZ",
                "trade_date": "2026-07-14",
                "analysts": ["market"],
            }
        )

    assert exc_info.value.run["id"] == first["id"]


def test_events_are_monotonic_and_running_jobs_fail_on_recovery(tmp_path: Path):
    store = AdminStore(tmp_path / "admin.sqlite3")
    run = store.create_analysis_run(
        {
            "owner_key": "uid:u1",
            "ticker": "600519.SH",
            "stock_name": "贵州茅台",
            "trade_date": "2026-07-14",
            "asset_type": "stock",
            "analysts": ["market"],
        }
    )

    assert store.claim_analysis_run(run["id"])["status"] == "running"
    first = store.append_analysis_event(run["id"], AnalysisEvent("run_started", {}))
    second = store.append_analysis_event(
        run["id"], AnalysisEvent("agent_message", {"content": "working"})
    )

    assert [first["seq"], second["seq"]] == [1, 2]
    assert [item["seq"] for item in store.list_analysis_events(run["id"])] == [1, 2]
    assert store.mark_interrupted_runs_failed() == 1
    interrupted = store.get_analysis_run(run["id"])
    assert interrupted["status"] == "failed"
    assert interrupted["error_type"] == "WorkerInterrupted"


def test_report_owner_filter_and_stock_name_are_persisted(tmp_path: Path):
    store = AdminStore(tmp_path / "admin.sqlite3")
    report = store.save_analysis_report(
        {
            "ticker": "600519.SH",
            "stock_name": "贵州茅台",
            "trade_date": "2026-07-14",
            "analysts": ["market"],
            "sections": {"market_report": "# 市场"},
            "owner_key": "uid:u1",
            "owner_uid": "u1",
            "owner_email": "USER@example.com",
        }
    )

    assert report["stock_name"] == "贵州茅台"
    assert report["owner_email"] == "user@example.com"
    assert store.list_analysis_reports(owner_key="uid:u1")[0]["id"] == report["id"]
    assert store.list_analysis_reports(owner_key="uid:u2") == []
    assert store.get_analysis_report(report["id"], owner_key="uid:u2") is None


def test_legacy_report_owner_is_backfilled_during_store_upgrade(tmp_path: Path):
    db_path = tmp_path / "admin.sqlite3"
    store = AdminStore(db_path)
    report = store.save_analysis_report(
        {
            "ticker": "600519.SH",
            "trade_date": "2026-07-13",
            "analysts": ["market"],
            "sections": {"market_report": "# 市场"},
            "owner": "Legacy@Example.com",
        }
    )
    assert report["owner_key"] == ""

    upgraded = AdminStore(db_path)

    visible = upgraded.list_analysis_reports(owner_key="email:legacy@example.com")
    assert [item["id"] for item in visible] == [report["id"]]
    assert visible[0]["owner_email"] == "legacy@example.com"


def test_runtime_settings_have_database_defaults_and_validate_updates(tmp_path: Path):
    store = AdminStore(tmp_path / "admin.sqlite3")

    defaults = store.get_runtime_settings()
    assert defaults.analysis_concurrency_limit == 2
    assert defaults.analysis_queue_limit == 20
    assert defaults.accept_new_tasks is True
    assert defaults.warning == ""

    updated = store.update_runtime_settings(
        {
            "analysis_concurrency_limit": 4,
            "analysis_queue_limit": 30,
            "accept_new_tasks": False,
        },
        updated_by="uid:admin",
    )
    assert updated.analysis_concurrency_limit == 4
    assert updated.analysis_queue_limit == 30
    assert updated.accept_new_tasks is False
    assert updated.updated_by == "uid:admin"

    with pytest.raises(ValueError, match="between 1 and 8"):
        store.update_runtime_settings(
            {
                "analysis_concurrency_limit": 9,
                "analysis_queue_limit": 30,
                "accept_new_tasks": True,
            },
            updated_by="uid:admin",
        )


def test_paused_submission_and_queue_limit_are_enforced_in_store(tmp_path: Path):
    store = AdminStore(tmp_path / "admin.sqlite3")
    store.update_runtime_settings(
        {
            "analysis_concurrency_limit": 1,
            "analysis_queue_limit": 1,
            "accept_new_tasks": False,
        },
        updated_by="uid:admin",
    )

    with pytest.raises(TaskSubmissionPaused):
        store.create_analysis_run(
            {
                "owner_key": "uid:u1",
                "ticker": "600519.SH",
                "trade_date": "2026-07-16",
                "analysts": ["market"],
            }
        )

    store.update_runtime_settings(
        {
            "analysis_concurrency_limit": 1,
            "analysis_queue_limit": 1,
            "accept_new_tasks": True,
        },
        updated_by="uid:admin",
    )
    store.create_analysis_run(
        {
            "owner_key": "uid:u1",
            "ticker": "600519.SH",
            "trade_date": "2026-07-16",
            "analysts": ["market"],
        }
    )
    with pytest.raises(QueueLimitReached):
        store.create_analysis_run(
            {
                "owner_key": "uid:u2",
                "ticker": "000001.SZ",
                "trade_date": "2026-07-16",
                "analysts": ["market"],
            }
        )


def test_claim_next_analysis_run_is_fifo(tmp_path: Path):
    store = AdminStore(tmp_path / "admin.sqlite3")
    first = store.create_analysis_run(
        {
            "owner_key": "uid:u1",
            "ticker": "600519.SH",
            "trade_date": "2026-07-16",
            "analysts": ["market"],
        }
    )
    store.create_analysis_run(
        {
            "owner_key": "uid:u2",
            "ticker": "000001.SZ",
            "trade_date": "2026-07-16",
            "analysts": ["market"],
        }
    )

    claimed = store.claim_next_analysis_run()

    assert claimed["id"] == first["id"]
    assert claimed["status"] == "running"
    assert store.count_running_analysis_runs() == 1
    assert store.count_queued_analysis_runs() == 1
