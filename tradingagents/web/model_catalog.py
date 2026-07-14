"""Provider-backed model discovery for the admin workbench."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import time
from typing import Any

import requests


class ModelCatalogError(RuntimeError):
    """Raised when a provider model catalog cannot be fetched or parsed."""


class CatalogUnsupported(ModelCatalogError):
    """Raised when a provider has no reliable callable-model list endpoint."""


@dataclass(frozen=True)
class ModelInfo:
    id: str
    display_name: str

    def as_dict(self) -> dict[str, str]:
        return {"id": self.id, "display_name": self.display_name}


_DEFAULT_ENDPOINTS = {
    "deepseek": "https://api.deepseek.com/models",
    "openai": "https://api.openai.com/v1/models",
    "anthropic": "https://api.anthropic.com/v1/models",
    "google": "https://generativelanguage.googleapis.com/v1beta/models",
    "kimi": "https://api.moonshot.cn/v1/models",
}


class ModelCatalog:
    def __init__(
        self,
        session: Any | None = None,
        ttl_seconds: int = 300,
    ):
        self.session = session or requests.Session()
        self.ttl_seconds = int(ttl_seconds)
        self._cache: dict[tuple[str, str, str], tuple[float, list[ModelInfo]]] = {}

    def fetch(
        self,
        provider: str,
        api_key: str,
        base_url: str | None = None,
    ) -> list[ModelInfo]:
        provider = str(provider or "").strip().lower()
        api_key = str(api_key or "")
        if provider in {"qwen-cn", "glm-cn"}:
            raise CatalogUnsupported("该供应商没有可靠的可调用模型列表接口，请手动填写模型 ID。")
        if not api_key:
            raise ModelCatalogError("API Key is required to fetch models")

        endpoint = self._endpoint(provider, base_url)
        fingerprint = hashlib.sha256(api_key.encode("utf-8")).hexdigest()
        cache_key = (provider, endpoint, fingerprint)
        cached = self._cache.get(cache_key)
        if cached is not None and time.monotonic() - cached[0] < self.ttl_seconds:
            return list(cached[1])

        headers = self._headers(provider, api_key)
        try:
            response = self.session.get(endpoint, headers=headers, timeout=10)
            response.raise_for_status()
            payload = response.json()
            models = self._parse(provider, payload)
        except ModelCatalogError:
            raise
        except Exception as exc:  # noqa: BLE001 - normalize provider/network errors
            raise ModelCatalogError(f"获取模型列表失败：{exc}") from exc
        if not models:
            raise ModelCatalogError("供应商返回的模型列表为空")
        self._cache[cache_key] = (time.monotonic(), models)
        return list(models)

    def _endpoint(self, provider: str, base_url: str | None) -> str:
        if provider == "openai_compatible":
            if not base_url:
                raise ModelCatalogError("OpenAI 兼容服务需要 Base URL")
            return f"{str(base_url).rstrip('/')}/models"
        if provider in {"deepseek", "kimi"} and base_url:
            return f"{str(base_url).rstrip('/')}/models"
        endpoint = _DEFAULT_ENDPOINTS.get(provider)
        if endpoint is None:
            raise CatalogUnsupported("该供应商暂不支持实时模型目录，请手动填写模型 ID。")
        return endpoint

    def _headers(self, provider: str, api_key: str) -> dict[str, str]:
        if provider == "anthropic":
            return {
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "accept": "application/json",
            }
        if provider == "google":
            return {"x-goog-api-key": api_key, "accept": "application/json"}
        return {
            "Authorization": f"Bearer {api_key}",
            "accept": "application/json",
        }

    def _parse(self, provider: str, payload: Any) -> list[ModelInfo]:
        if not isinstance(payload, dict):
            raise ModelCatalogError("供应商返回了无效的模型列表格式")
        if provider == "google":
            raw_models = payload.get("models") or []
            parsed = []
            for item in raw_models:
                if not isinstance(item, dict):
                    continue
                methods = item.get("supportedGenerationMethods") or item.get(
                    "supported_actions"
                ) or []
                if "generateContent" not in methods:
                    continue
                model_id = str(item.get("baseModelId") or item.get("name") or "")
                model_id = model_id.removeprefix("models/").strip()
                if model_id:
                    parsed.append(
                        ModelInfo(
                            model_id,
                            str(item.get("displayName") or model_id),
                        )
                    )
            return self._deduplicate(parsed)

        raw_models = payload.get("data") or []
        parsed = []
        for item in raw_models:
            if not isinstance(item, dict):
                continue
            model_id = str(item.get("id") or "").strip()
            if model_id:
                parsed.append(
                    ModelInfo(
                        model_id,
                        str(item.get("display_name") or item.get("name") or model_id),
                    )
                )
        return self._deduplicate(parsed)

    def _deduplicate(self, models: list[ModelInfo]) -> list[ModelInfo]:
        unique: dict[str, ModelInfo] = {}
        for model in models:
            unique.setdefault(model.id, model)
        return list(unique.values())


_CATALOG: ModelCatalog | None = None


def get_model_catalog() -> ModelCatalog:
    global _CATALOG
    if _CATALOG is None:
        _CATALOG = ModelCatalog()
    return _CATALOG
