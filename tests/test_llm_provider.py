import importlib

import pytest

ENV = ("LLM_PROVIDER", "ANYMODEL_API_KEY", "DEEPSEEK_API_KEY", "OPENROUTER_API_KEY",
       "ANYMODEL_BASE_URL", "ANYMODEL_MODEL_TECH", "ANYMODEL_MODEL_GENERAL")


@pytest.fixture
def load(monkeypatch):
    monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: False)

    def _load(**env):
        for k in ENV:
            monkeypatch.delenv(k, raising=False)
        for k, v in env.items():
            monkeypatch.setenv(k, v)
        from sokrat import config

        return importlib.reload(config).settings

    return _load


def test_anymodel_is_default_when_key_set(load):
    s = load(ANYMODEL_API_KEY="k", DEEPSEEK_API_KEY="old")
    assert s.LLM_PROVIDER == "anymodel" and s.LLM_API_KEY == "k"
    assert s.LLM_BASE_URL == "https://anymodel.org/v1"
    assert s.MODEL_TECH == "ds/deepseek-v4-pro" and s.MODEL_GENERAL == "ds/deepseek-v4-flash"


def test_overrides_and_fallbacks(load):
    s = load(LLM_PROVIDER="anymodel", ANYMODEL_API_KEY="k", ANYMODEL_MODEL_TECH="am/free")
    assert s.MODEL_TECH == "am/free"
    assert load(DEEPSEEK_API_KEY="d").LLM_PROVIDER == "deepseek"
    assert load().LLM_PROVIDER == "openrouter"
