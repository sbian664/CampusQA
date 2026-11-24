import pytest
from cryptography.fernet import Fernet

from src.identity import UserStore
from src.personal_llm_config import PersonalLLMConfigStore


def test_cloudbase_choice_preserves_personal_settings_and_uses_the_same_user_store(tmp_path, monkeypatch):
    monkeypatch.setattr("src.user_llm_config.LLM_CONFIG_ALLOWED_HOSTS", ["api.deepseek.com", "function.example.test"])
    users = UserStore(tmp_path / "users.db")
    first = users.login("test", "first")["user_id"]
    other = users.login("test", "other")["user_id"]
    bridge = {"provider": "custom", "base_url": "https://function.example.test/v1",
              "model": "hy4-flash", "api_key": "bridge-only"}
    personal = {"source": "personal", "provider": "deepseek", "base_url": "https://api.deepseek.com/v1",
                "model": "deepseek-chat", "api_key": "personal-only"}
    store = PersonalLLMConfigStore(users, Fernet.generate_key(), cloudbase_config=bridge)
    store.save(first, personal)
    public = store.save(first, {"source": "cloudbase", "base_url": "https://ignored.example"})
    assert public["source"] == "cloudbase"
    assert public["cloudbase_available"] is True
    assert public["provider"] == "deepseek"
    assert "api_key" not in public and "bridge-only" not in str(public)
    assert store.effective(first, {}) == bridge
    assert store.effective(other, {"model": "server"}) == {"model": "server"}
    store.save(first, {**personal, "api_key": ""})
    assert store.effective(first, {})["api_key"] == "personal-only"


def test_cloudbase_choice_requires_a_configured_cloud_function(tmp_path):
    users = UserStore(tmp_path / "users.db")
    user = users.login("test", "first")["user_id"]
    store = PersonalLLMConfigStore(users, Fernet.generate_key())
    with pytest.raises(ValueError, match="CloudBase"):
        store.save(user, {"source": "cloudbase"})
    assert store.public_config(user)["source"] == "server"
