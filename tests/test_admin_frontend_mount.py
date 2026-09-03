import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi import FastAPI
from fastapi.testclient import TestClient

import server


class AdminFrontendMountTests(unittest.TestCase):
    def test_admin_frontend_is_served_under_admin_path_with_assets(self):
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "index.html").write_text(
                '<!doctype html><script type="module" src="/admin/assets/app.js"></script>',
                encoding="utf-8",
            )
            assets = root / "assets"
            assets.mkdir()
            (assets / "app.js").write_text("console.log('admin')", encoding="utf-8")

            app = FastAPI()
            mount_admin_frontend = getattr(server, "mount_admin_frontend", None)
            self.assertIsNotNone(mount_admin_frontend)
            mount_admin_frontend(app, root)

            with TestClient(app) as client:
                index = client.get("/admin/")
                asset = client.get("/admin/assets/app.js")

            self.assertEqual(index.status_code, 200)
            self.assertIn("/admin/assets/app.js", index.text)
            self.assertEqual(asset.status_code, 200)
            self.assertIn("console.log('admin')", asset.text)


if __name__ == "__main__":
    unittest.main()
