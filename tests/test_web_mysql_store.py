import os

import pytest

from tests.test_web_store_contract import assert_store_contract
from tradingagents.web.mysql_store import MySQLApplicationStore, parse_mysql_url


def test_parse_mysql_url_decodes_credentials_and_port():
    parsed = parse_mysql_url(
        "mysql+pymysql://user%40name:p%40ss@db.internal:3307/tcb"
    )

    assert parsed == {
        "host": "db.internal",
        "port": 3307,
        "user": "user@name",
        "password": "p@ss",
        "database": "tcb",
    }


@pytest.mark.parametrize("url", ["sqlite:///x.db", "mysql://host-only"])
def test_parse_mysql_url_rejects_invalid_urls(url):
    with pytest.raises(ValueError):
        parse_mysql_url(url)


@pytest.mark.integration
def test_mysql_store_matches_application_store_contract():
    database_url = os.environ.get("TRADINGAGENTS_TEST_MYSQL_URL")
    if not database_url:
        pytest.skip("TRADINGAGENTS_TEST_MYSQL_URL is not configured")

    store = MySQLApplicationStore(database_url, b"m" * 32)
    with store._connect() as conn:
        with conn.cursor() as cursor:
            cursor.execute("SET FOREIGN_KEY_CHECKS = 0")
            for table in (
                "analysis_run_events",
                "active_analysis_owners",
                "analysis_runs",
                "analysis_reports",
                "model_configs",
                "access_whitelist",
                "app_users",
                "admin_sessions",
            ):
                cursor.execute(f"DELETE FROM {table}")
            cursor.execute("SET FOREIGN_KEY_CHECKS = 1")
        conn.commit()
    assert_store_contract(store)
