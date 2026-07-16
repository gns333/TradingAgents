import base64

import pytest

from tradingagents.web.runtime import (
    RuntimeConfigurationError,
    load_web_runtime_config,
)


def test_local_runtime_is_the_zero_configuration_default():
    config = load_web_runtime_config({})

    assert config.mode == "local"
    assert config.database_url.endswith(".tradingagents/web_admin.sqlite3")
    assert config.cloudbase_env_id == ""
    assert config.master_key is None


def test_cloudbase_runtime_requires_database_auth_and_master_key():
    with pytest.raises(RuntimeConfigurationError) as exc_info:
        load_web_runtime_config({"TRADINGAGENTS_RUNTIME": "cloudbase"})

    message = str(exc_info.value)
    assert "TRADINGAGENTS_DATABASE_URL" in message
    assert "TRADINGAGENTS_CLOUDBASE_ENV_ID" in message
    assert "TRADINGAGENTS_CLOUDBASE_PUBLISHABLE_KEY" in message
    assert "TRADINGAGENTS_MASTER_KEY" in message


def test_cloudbase_runtime_decodes_a_32_byte_master_key():
    encoded = base64.urlsafe_b64encode(b"k" * 32).decode("ascii")

    config = load_web_runtime_config(
        {
            "TRADINGAGENTS_RUNTIME": "cloudbase",
            "TRADINGAGENTS_DATABASE_URL": "mysql+pymysql://user:pass@db/tcb",
            "TRADINGAGENTS_CLOUDBASE_ENV_ID": "env-123",
            "TRADINGAGENTS_CLOUDBASE_REGION": "ap-shanghai",
            "TRADINGAGENTS_CLOUDBASE_PUBLISHABLE_KEY": "public-key",
            "TRADINGAGENTS_MASTER_KEY": encoded,
        }
    )

    assert config.mode == "cloudbase"
    assert config.master_key == b"k" * 32
    assert config.cloudbase_publishable_key == "public-key"


@pytest.mark.parametrize("value", ["invalid", "YQ=="])
def test_cloudbase_runtime_rejects_invalid_master_keys(value):
    with pytest.raises(RuntimeConfigurationError, match="32 bytes|base64"):
        load_web_runtime_config(
            {
                "TRADINGAGENTS_RUNTIME": "cloudbase",
                "TRADINGAGENTS_DATABASE_URL": "mysql+pymysql://user:pass@db/tcb",
                "TRADINGAGENTS_CLOUDBASE_ENV_ID": "env-123",
                "TRADINGAGENTS_CLOUDBASE_PUBLISHABLE_KEY": "public-key",
                "TRADINGAGENTS_MASTER_KEY": value,
            }
        )
