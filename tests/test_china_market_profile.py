"""Mainland-China market profile defaults."""

import importlib
import unittest

import tradingagents.default_config as default_config_module


def _reload_with_env(monkeypatch, **overrides):
    for key in list(default_config_module._ENV_OVERRIDES):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.delenv("TRADINGAGENTS_MARKET_PROFILE", raising=False)
    for key, value in overrides.items():
        monkeypatch.setenv(key, value)
    return importlib.reload(default_config_module)


class ChinaMarketProfileTests(unittest.TestCase):
    def tearDown(self):
        import os

        for key in list(default_config_module._ENV_OVERRIDES):
            os.environ.pop(key, None)
        os.environ.pop("TRADINGAGENTS_MARKET_PROFILE", None)
        importlib.reload(default_config_module)

    def test_china_mainland_profile_uses_deepseek_and_china_data_vendors(self):
        import os

        os.environ["TRADINGAGENTS_MARKET_PROFILE"] = "china_mainland"
        dc = importlib.reload(default_config_module)

        self.assertEqual(dc.DEFAULT_CONFIG["market_profile"], "china_mainland")
        self.assertEqual(dc.DEFAULT_CONFIG["llm_provider"], "deepseek")
        self.assertEqual(dc.DEFAULT_CONFIG["quick_think_llm"], "deepseek-v4-flash")
        self.assertEqual(dc.DEFAULT_CONFIG["deep_think_llm"], "deepseek-v4-pro")
        self.assertEqual(dc.DEFAULT_CONFIG["output_language"], "Chinese")
        self.assertEqual(dc.DEFAULT_CONFIG["data_vendors"]["core_stock_apis"], "akshare,yfinance")
        self.assertEqual(dc.DEFAULT_CONFIG["data_vendors"]["technical_indicators"], "akshare,yfinance")
        self.assertEqual(dc.DEFAULT_CONFIG["data_vendors"]["fundamental_data"], "akshare,yfinance")
        self.assertEqual(dc.DEFAULT_CONFIG["data_vendors"]["news_data"], "akshare,yfinance")
        self.assertEqual(dc.DEFAULT_CONFIG["data_vendors"]["macro_data"], "akshare")

    def test_explicit_env_overrides_china_profile_defaults(self):
        import os

        os.environ["TRADINGAGENTS_MARKET_PROFILE"] = "china_mainland"
        os.environ["TRADINGAGENTS_LLM_PROVIDER"] = "qwen-cn"
        os.environ["TRADINGAGENTS_OUTPUT_LANGUAGE"] = "中文"
        dc = importlib.reload(default_config_module)

        self.assertEqual(dc.DEFAULT_CONFIG["market_profile"], "china_mainland")
        self.assertEqual(dc.DEFAULT_CONFIG["llm_provider"], "qwen-cn")
        self.assertEqual(dc.DEFAULT_CONFIG["output_language"], "中文")

    def test_unknown_market_profile_raises(self):
        import os

        os.environ["TRADINGAGENTS_MARKET_PROFILE"] = "mars"
        with self.assertRaisesRegex(ValueError, "TRADINGAGENTS_MARKET_PROFILE"):
            importlib.reload(default_config_module)


if __name__ == "__main__":
    unittest.main()
