from tradingagents.web.admin_store import AdminStore
from tradingagents.web.events import AnalysisEvent


def assert_store_contract(store):
    settings = store.get_runtime_settings()
    assert settings.analysis_concurrency_limit == 2

    store.upsert_whitelist({"email": "user@example.com", "status": "active"})
    assert store.is_identity_allowed(email="user@example.com")

    model = store.save_model_config(
        {
            "provider": "deepseek",
            "display_name": "DeepSeek",
            "quick_model": "quick",
            "deep_model": "deep",
            "api_key": "sk-contract-secret",
            "is_default": True,
        }
    )
    assert "sk-contract-secret" not in str(model)
    assert store.get_default_runtime_model().api_key == "sk-contract-secret"

    run = store.create_analysis_run(
        {
            "owner_key": "uid:u1",
            "owner_uid": "u1",
            "ticker": "600519.SH",
            "trade_date": "2026-07-16",
            "analysts": ["market"],
        }
    )
    claimed = store.claim_next_analysis_run()
    assert claimed["id"] == run["id"]
    event = store.append_analysis_event(
        run["id"],
        AnalysisEvent("run_started", {}),
    )
    assert event["seq"] == 1
    store.fail_analysis_run(run["id"], "ContractFailure", "expected")
    assert store.get_analysis_run(run["id"])["status"] == "failed"


def test_sqlite_store_matches_application_store_contract(tmp_path):
    assert_store_contract(AdminStore(tmp_path / "contract.sqlite3"))
