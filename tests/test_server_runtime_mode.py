import ast
import unittest
from pathlib import Path


class ServerRuntimeModeTests(unittest.TestCase):
    def test_production_entrypoint_disables_uvicorn_reload(self):
        tree = ast.parse(Path("server.py").read_text(encoding="utf-8"))
        reload_values = []

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if not isinstance(node.func, ast.Attribute) or node.func.attr != "run":
                continue
            if not isinstance(node.func.value, ast.Name) or node.func.value.id != "uvicorn":
                continue
            for keyword in node.keywords:
                if keyword.arg == "reload":
                    reload_values.append(keyword.value)

        self.assertEqual(len(reload_values), 1)
        self.assertIsInstance(reload_values[0], ast.Constant)
        self.assertIs(reload_values[0].value, False)


if __name__ == "__main__":
    unittest.main()
