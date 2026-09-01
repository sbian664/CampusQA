import ast
from pathlib import Path
import unittest


class LLMConfigAuthRemovalTests(unittest.TestCase):
    def test_llm_config_endpoints_have_no_token_dependency(self):
        source = Path(__file__).parents[1].joinpath("server.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        endpoint_paths = {"/api/llm-config", "/api/llm-config/test"}

        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for decorator in node.decorator_list:
                if not isinstance(decorator, ast.Call):
                    continue
                if not isinstance(decorator.func, ast.Attribute):
                    continue
                if not decorator.args or not isinstance(decorator.args[0], ast.Constant):
                    continue
                if decorator.args[0].value not in endpoint_paths:
                    continue
                dependencies = [keyword.value for keyword in decorator.keywords if keyword.arg == "dependencies"]
                self.assertEqual(
                    dependencies,
                    [],
                    f"{node.name} must not require LLM_CONFIG_TOKEN",
                )

    def test_llm_config_test_preserves_saved_key_when_input_is_blank(self):
        source = Path(__file__).parents[1].joinpath("server.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        endpoint = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == "test_llm_config"
        )

        resolve_calls = [
            node
            for node in ast.walk(endpoint)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "resolve"
        ]
        self.assertEqual(len(resolve_calls), 1)
        self.assertNotIn(
            "preserve_existing_key",
            {keyword.arg for keyword in resolve_calls[0].keywords},
        )


if __name__ == "__main__":
    unittest.main()
