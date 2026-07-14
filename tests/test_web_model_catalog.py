from tradingagents.web.model_catalog import CatalogUnsupported, ModelCatalog


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self.payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return FakeResponse(self.payload)


def test_openai_compatible_catalog_uses_models_endpoint_and_bearer_token():
    session = FakeSession({"data": [{"id": "deepseek-v4-flash"}]})

    models = ModelCatalog(session=session).fetch("deepseek", "sk-test")

    assert [model.id for model in models] == ["deepseek-v4-flash"]
    url, kwargs = session.calls[0]
    assert url == "https://api.deepseek.com/models"
    assert kwargs["headers"]["Authorization"] == "Bearer sk-test"


def test_anthropic_catalog_uses_vendor_headers():
    session = FakeSession(
        {"data": [{"id": "claude-sonnet", "display_name": "Claude Sonnet"}]}
    )

    models = ModelCatalog(session=session).fetch("anthropic", "ant-test")

    assert models[0].display_name == "Claude Sonnet"
    _, kwargs = session.calls[0]
    assert kwargs["headers"]["x-api-key"] == "ant-test"
    assert kwargs["headers"]["anthropic-version"] == "2023-06-01"


def test_gemini_filters_non_generation_models_and_uses_header_key():
    session = FakeSession(
        {
            "models": [
                {
                    "name": "models/gemini-x",
                    "displayName": "Gemini X",
                    "supportedGenerationMethods": ["generateContent"],
                },
                {
                    "name": "models/embed-x",
                    "displayName": "Embed X",
                    "supportedGenerationMethods": ["embedContent"],
                },
            ]
        }
    )

    models = ModelCatalog(session=session).fetch("google", "google-test")

    assert [model.id for model in models] == ["gemini-x"]
    _, kwargs = session.calls[0]
    assert kwargs["headers"]["x-goog-api-key"] == "google-test"


def test_qwen_catalog_is_explicitly_manual_instead_of_static_fallback():
    try:
        ModelCatalog(session=FakeSession({})).fetch("qwen-cn", "key")
    except CatalogUnsupported as exc:
        assert "手动" in str(exc)
    else:
        raise AssertionError("qwen-cn must use manual model ids")
