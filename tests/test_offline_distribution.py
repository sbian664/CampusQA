import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from unittest.mock import patch
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('offline', Path(__file__).parents[1] / 'deploy/docker/offline.py')
offline = importlib.util.module_from_spec(spec)
spec.loader.exec_module(offline)


class OfflineDistributionTests(unittest.TestCase):
    def test_management_keeps_releases_together_and_installations_separate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            binary = root / 'bin'
            binary.mkdir()
            log = root / 'docker-commands'
            docker = binary / 'docker'
            docker.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$DOCKER_TEST_LOG"\n')
            docker.chmod(0o755)
            # The target installer requires Linux sha256sum. Supply its exact stdin
            # digest behavior for this shell-unit test on hosts such as macOS.
            checksum = binary / 'sha256sum'
            checksum.write_text('#!' + sys.executable + '\nimport hashlib,sys\nprint(hashlib.sha256(sys.stdin.buffer.read()).hexdigest()+"  -")\n')
            checksum.chmod(0o755)
            env = {**os.environ, 'PATH': str(binary) + os.pathsep + os.environ['PATH'],
                   'DOCKER_TEST_LOG': str(log)}
            source = Path(__file__).parents[1] / 'deploy/docker/release'
            releases = [root / 'release-a', root / 'release-b']
            instances = [root / 'instance-a', root / 'instance-b']
            for instance in instances:
                instance.mkdir()
            for release in releases:
                shutil.copytree(source, release / 'scripts')
                (release / 'release.env').write_text('CAMPUSQA_IMAGE=test-image\n')
            for release, instance, action in [(releases[0], instances[0], 'up'),
                                               (releases[1], instances[0], 'down'),
                                               (releases[1], instances[1], 'up')]:
                subprocess.run(['bash', str(release / 'scripts/manage.sh'), str(instance), action],
                               env=env, check=True)
            projects = [line.split()[2] for line in log.read_text().splitlines()]
            self.assertEqual(projects[0], projects[1])
            self.assertNotEqual(projects[0], projects[2])

    def test_checksum_failure_and_escape_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for directory in ('embedding', 'reranker'):
                (root / directory).mkdir()
                (root / directory / 'model.safetensors').write_bytes(b'fixed test weight')
            files = [{'path': str(p.relative_to(root)), 'size': p.stat().st_size, 'sha256': offline.digest(p)}
                     for p in sorted(root.rglob('*.safetensors'))]
            (root / 'manifest.json').write_text(json.dumps({'models': [{'directory': d} for d in ('embedding', 'reranker')], 'files': files}))
            self.assertEqual(offline.verify_models(root)['verified_files'], 2)
            (root / files[0]['path']).write_bytes(b'damaged')
            with self.assertRaisesRegex(ValueError, 'corrupt'):
                offline.verify_models(root)
            with self.assertRaisesRegex(ValueError, 'Unsafe'):
                offline.child(root, '../outside')

    def test_migration_preserves_identity_closes_gate_and_does_not_rewrite_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            (root / 'documents').mkdir()
            (root / 'cache').mkdir()
            (root / 'documents/guide.txt').write_text('original body /old/documents/guide.txt')
            meta = root / 'cache/kb_metadata.json'
            meta.write_text(json.dumps({'/old/documents/guide.txt': {'document_id': 'stable-identity', 'chunk_ids': ['old']}}))
            session = root / 'cache/old-session.json'
            session.write_text('{"message":"/old/documents/guide.txt"}')
            before = meta.read_text()
            offline.migrate(root, '/old/documents')
            self.assertEqual(before, meta.read_text())
            offline.migrate(root, '/old/documents', True)
            self.assertEqual(json.loads(meta.read_text())[str(root / 'documents/guide.txt')]['document_id'], 'stable-identity')
            self.assertEqual(json.loads((root / 'cache/index_state.json').read_text())['status'], 'repair_required')
            self.assertIn('/old/documents/guide.txt', session.read_text())
            with self.assertRaisesRegex(ValueError, 'already applied'):
                offline.migrate(root, '/old/documents', True)

    def test_active_data_is_locked_and_restore_requires_empty_destination(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data = root / 'data'
            with offline.data_lock(data):
                with self.assertRaisesRegex(RuntimeError, 'Data is in use'):
                    offline.backup_data(data, root / 'backup')
            (data / 'record').write_text('preserved')
            offline.backup_data(data, root / 'backup')
            restored = root / 'restored'
            offline.restore_data(restored, root / 'backup')
            self.assertEqual((restored / 'record').read_text(), 'preserved')
            with self.assertRaisesRegex(ValueError, 'empty destination'):
                offline.restore_data(restored, root / 'backup')

    def test_missing_document_does_not_mutate_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'cache').mkdir()
            meta = root / 'cache/kb_metadata.json'
            meta.write_text(json.dumps({'/old/missing.txt': {'document_id': 'id'}}))
            before = meta.read_text()
            with self.assertRaisesRegex(ValueError, 'Missing source'):
                offline.migrate(root, '/old', True)
            self.assertEqual(meta.read_text(), before)
            self.assertFalse((root / 'cache/index_state.json').exists())


    def test_identity_config_requires_wechat_and_stable_fernet_key(self):
        import base64
        valid = {'WECHAT_APP_ID': 'fixture-app', 'WECHAT_APP_SECRET': 'fixture-only',
                 'USER_LLM_ENCRYPTION_KEY': base64.urlsafe_b64encode(b'x' * 32).decode()}
        with patch.dict(os.environ, valid, clear=True):
            offline.verify_identity_config()
        for key in valid:
            with self.subTest(missing=key), patch.dict(os.environ, {k:v for k,v in valid.items() if k != key}, clear=True):
                with self.assertRaisesRegex(ValueError, key):
                    offline.verify_identity_config()
        with patch.dict(os.environ, {**valid, 'USER_LLM_ENCRYPTION_KEY': 'invalid'}, clear=True):
            with self.assertRaisesRegex(ValueError, 'USER_LLM_ENCRYPTION_KEY'):
                offline.verify_identity_config()

    def test_managed_migration_guard_leaves_all_metadata_unchanged(self):
        cases = [
            {'active': 'a' * 32},
            {'active': 'legacy', 'previous': 'b' * 32},
            {'active': 'legacy', 'building': 'c' * 32},
            {'active': 'unknown'},
            [],
            'broken-json',
            None,  # orphaned generation with no pointer
        ]
        for registry in cases:
            with self.subTest(registry=registry), tempfile.TemporaryDirectory() as tmp:
                data = Path(tmp)
                versions = data / 'cache/index_versions'
                versions.mkdir(parents=True)
                if registry is None:
                    (versions / ('a' * 32)).mkdir()
                    (versions / ('a' * 32) / 'metadata.json').write_text('{}')
                else:
                    (versions / 'registry.json').write_text(registry if isinstance(registry, str) else json.dumps(registry))
                (data / 'cache/kb_metadata.json').write_text('{}')
                (data / 'cache/index_state.json').write_text('{"status":"ready"}')
                before = {str(p): p.read_bytes() for p in data.rglob('*') if p.is_file()}
                for apply in (False, True):
                    with self.assertRaisesRegex(ValueError, 'Managed index migration is unsupported'):
                        offline.migrate(data, '/old/documents', apply)
                after = {str(p): p.read_bytes() for p in data.rglob('*') if p.is_file() and p.name != '.instance.lock'}
                self.assertEqual(before, after)
                self.assertFalse((data / 'migration-report.json').exists())

    def test_legacy_only_registry_can_migrate_but_hidden_generations_cannot(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            versions = data / 'cache/index_versions'
            versions.mkdir(parents=True)
            (versions / 'registry.json').write_text(json.dumps({'active': 'legacy', 'previous': None, 'building': None}))
            offline.require_legacy_migration(data)
            (versions / ('a' * 32)).mkdir()
            with self.assertRaisesRegex(ValueError, 'Managed index migration is unsupported'):
                offline.require_legacy_migration(data)

    def test_migration_guard_rejects_symlinked_registry(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            versions = data / 'cache/index_versions'
            versions.mkdir(parents=True)
            target = data / 'other.json'
            target.write_text('{"active":"legacy"}')
            (versions / 'registry.json').symlink_to(target)
            with self.assertRaisesRegex(ValueError, 'Managed index migration is unsupported'):
                offline.require_legacy_migration(data)

    def test_acceptance_headers_keep_user_and_admin_credentials_distinct(self):
        spec = importlib.util.spec_from_file_location('verify_image', Path(__file__).parents[1] / 'deploy/docker/verify_image.py')
        verify = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(verify)
        self.assertNotIn('Authorization', verify.request_headers())
        self.assertEqual(verify.request_headers(csrf='csrf', token='fixture-token'), {
            'Content-Type': 'application/json', 'X-CSRF-Token': 'csrf', 'Authorization': 'Bearer fixture-token'})
