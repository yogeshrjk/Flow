"""Tests never use real keys or overwrite a learner's local profiles."""
import pytest


@pytest.fixture(autouse=True)
def isolated_data(tmp_path, monkeypatch):
    from app.config import settings
    from app.conversation.engine import engine, Engine
    from tests.mock_provider import MockProvider
    from app import identity
    from app.main import _request_windows
    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    monkeypatch.setattr(identity, "_key", None)
    _request_windows.clear()
    engine.sessions.clear()
    # Explicit test doubles: application startup never installs a fake coach.
    monkeypatch.setattr(settings, "fish_api_key", "test-fish-key")
    monkeypatch.setattr(settings, "llm_default_response_mode", "groq_fish")
    original_init = Engine.__init__
    def test_engine_init(instance):
        original_init(instance)
        for key in ("groq_fish", "gemini_fish"):
            instance.providers.setdefault(key, MockProvider())
    monkeypatch.setattr(Engine, "__init__", test_engine_init)
    monkeypatch.setattr(engine, "providers", {"groq_fish": MockProvider(), "gemini_fish": MockProvider()})
    yield
    engine.sessions.clear()
