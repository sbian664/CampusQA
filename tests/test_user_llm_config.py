import json
import tempfile
import unittest
from pathlib import Path

from src.llm_client import OpenAICompatibleClient
from src.user_llm_config import UserLLMConfigStore, mask_api_key, normalize_llm_config


class UserLLMConfigTests(unittest.TestCase):
    def test_normalize_rejects_untrusted_base_url(self):
        with self.assertRaises(ValueError):
            normalize_llm_config({
                "provider": "custom",
                "api_key": "sk-test",
                "model": "custom-model",
                "base_url": "http://169.254.169.254/latest/meta-data",
            })

    def test_normalize_accepts_openai_compatible_provider(self):
        config = normalize_llm_config({
            "provider": "openai",
            "api_key": "sk-test",
            "model": "gpt-4o-mini",
            "base_url": "https://api.openai.com/v1/",
        })

        self.assertEqual(config["provider"], "openai")
        self.assertEqual(config["base_url"], "https://api.openai.com/v1")

    def test_store_returns_masked_key_and_preserves_key_when_mask_sent(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "user_llm_config.json"
            store = UserLLMConfigStore(path=path, default_config={
                "provider": "deepseek",
                "api_key": "sk-default",
                "model": "deepseek-chat",
                "base_url": "https://api.deepseek.com/v1",
            })

            store.save({
                "provider": "deepseek",
                "api_key": "sk-real-secret",
                "model": "deepseek-chat",
                "base_url": "https://api.deepseek.com/v1",
            })
            public = store.public_config()
            self.assertEqual(public["api_key"], mask_api_key("sk-real-secret"))
            self.assertNotEqual(public["api_key"], "sk-real-secret")

            store.update({
                "provider": "deepseek",
                "api_key": public["api_key"],
                "model": "deepseek-reasoner",
                "base_url": "https://api.deepseek.com/v1",
            })
            self.assertEqual(store.get()["api_key"], "sk-real-secret")
            self.assertEqual(store.get()["model"], "deepseek-reasoner")
            self.assertTrue(path.exists())
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["api_key"], "sk-real-secret")

    def test_resolve_candidate_does_not_persist_test_connection_config(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "user_llm_config.json"
            store = UserLLMConfigStore(path=path, default_config={
                "provider": "deepseek",
                "api_key": "sk-existing",
                "model": "deepseek-chat",
                "base_url": "https://api.deepseek.com/v1",
            })

            candidate = store.resolve({
                "provider": "openai",
                "api_key": "sk-new",
                "model": "gpt-4o-mini",
                "base_url": "https://api.openai.com/v1",
            })

            self.assertEqual(candidate["provider"], "openai")
            self.assertFalse(path.exists())

    def test_resolve_candidate_does_not_reuse_saved_api_key(self):
        with tempfile.TemporaryDirectory() as directory:
            store = UserLLMConfigStore(
                path=Path(directory) / "user_llm_config.json",
                default_config={
                    "provider": "deepseek",
                    "api_key": "sk-existing",
                    "model": "deepseek-chat",
                    "base_url": "https://api.deepseek.com/v1",
                },
            )

            with self.assertRaises(ValueError):
                store.resolve(
                    {
                        "provider": "openai",
                        "api_key": "",
                        "model": "gpt-4o-mini",
                        "base_url": "https://api.openai.com/v1",
                    },
                    preserve_existing_key=False,
                )

    def test_openai_compatible_client_uses_user_provider_model_and_key(self):
        client = OpenAICompatibleClient("qwen", {
            "api_key": "sk-user",
            "model": "qwen-plus",
            "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        })

        payload = client._build_payload([{"role": "user", "content": "你好"}])

        self.assertEqual(client.provider, "qwen")
        self.assertEqual(client.model, "qwen-plus")
        self.assertEqual(client.api_key, "sk-user")
        self.assertEqual(payload["model"], "qwen-plus")


if __name__ == "__main__":
    unittest.main()
