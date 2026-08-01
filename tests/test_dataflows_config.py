"""Config isolation: get/set must not leak nested-dict references."""

import copy
import unittest
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

import tradingagents.default_config as default_config
from tradingagents.dataflows.config import get_config, replace_config, set_config


@pytest.mark.unit
class DataflowsConfigIsolationTests(unittest.TestCase):
    def setUp(self):
        set_config(copy.deepcopy(default_config.DEFAULT_CONFIG))

    def test_get_config_returns_deep_copy(self):
        cfg = get_config()
        cfg["data_vendors"]["core_stock_apis"] = "alpha_vantage"
        cfg["tool_vendors"]["get_stock_data"] = "alpha_vantage"

        fresh = get_config()
        self.assertEqual(fresh["data_vendors"]["core_stock_apis"], "yfinance")
        self.assertNotIn("get_stock_data", fresh["tool_vendors"])

    def test_set_config_does_not_alias_caller_nested_dicts(self):
        custom = copy.deepcopy(default_config.DEFAULT_CONFIG)
        custom["data_vendors"]["core_stock_apis"] = "alpha_vantage"
        custom["tool_vendors"]["get_stock_data"] = "alpha_vantage"

        set_config(custom)

        custom["data_vendors"]["core_stock_apis"] = "yfinance"
        custom["tool_vendors"]["get_stock_data"] = "yfinance"

        fresh = get_config()
        self.assertEqual(fresh["data_vendors"]["core_stock_apis"], "alpha_vantage")
        self.assertEqual(fresh["tool_vendors"]["get_stock_data"], "alpha_vantage")

    def test_partial_nested_update_preserves_existing_defaults(self):
        set_config(
            {
                "data_vendors": {
                    "core_stock_apis": "alpha_vantage",
                }
            }
        )

        fresh = get_config()
        self.assertEqual(fresh["data_vendors"]["core_stock_apis"], "alpha_vantage")
        self.assertEqual(fresh["data_vendors"]["technical_indicators"], "yfinance")
        self.assertEqual(fresh["data_vendors"]["fundamental_data"], "yfinance")
        self.assertEqual(fresh["data_vendors"]["news_data"], "yfinance")

    def test_nested_dict_updates_merge_one_level_deep(self):
        set_config({"tool_vendors": {"get_stock_data": "alpha_vantage"}})
        set_config({"tool_vendors": {"get_news": "alpha_vantage"}})

        fresh = get_config()
        self.assertEqual(fresh["tool_vendors"]["get_stock_data"], "alpha_vantage")
        self.assertEqual(fresh["tool_vendors"]["get_news"], "alpha_vantage")

    def test_concurrent_tasks_keep_market_vendor_configs_isolated(self):
        barrier = Barrier(2)

        def configured_vendor(profile, vendor):
            config = copy.deepcopy(default_config.DEFAULT_CONFIG)
            config["market_profile"] = profile
            config["data_vendors"]["macro_data"] = vendor
            replace_config(config)
            barrier.wait(timeout=5)
            active = get_config()
            return active["market_profile"], active["data_vendors"]["macro_data"]

        with ThreadPoolExecutor(max_workers=2) as executor:
            china = executor.submit(configured_vendor, "china_mainland", "akshare")
            global_market = executor.submit(configured_vendor, "default", "fred")

        self.assertEqual(china.result(), ("china_mainland", "akshare"))
        self.assertEqual(global_market.result(), ("default", "fred"))
