from test2 import config


def test_get_memory_extraction_llm_disables_deepseek_thinking(monkeypatch):
    calls = []

    def fake_get_llm(provider, extra_body=None):
        calls.append((provider, extra_body))
        return object()

    monkeypatch.setattr(config, "get_llm", fake_get_llm)

    assert config.get_memory_extraction_llm("deepseek") is not None
    assert calls == [("deepseek", {"thinking": {"type": "disabled"}})]


def test_get_memory_extraction_llm_keeps_other_provider_unchanged(monkeypatch):
    calls = []

    def fake_get_llm(provider, extra_body=None):
        calls.append((provider, extra_body))
        return object()

    monkeypatch.setattr(config, "get_llm", fake_get_llm)

    assert config.get_memory_extraction_llm("openai") is not None
    assert calls == [("openai", None)]
