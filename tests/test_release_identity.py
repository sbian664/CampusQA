"""Release source-identity checks without a Docker daemon or real artifacts."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('build_release', Path(__file__).parents[1] / 'deploy/docker/build_release.py')
build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build)
BACKEND = 'a' * 40
ADMIN = 'b' * 40
LABELS = {'io.campusqa.backend-revision': BACKEND, 'io.campusqa.admin-revision': ADMIN}


def image(labels=LABELS):
    return {'Architecture': 'amd64', 'Os': 'linux', 'Config': {'Labels': labels}}


class ReleaseIdentityTests(unittest.TestCase):
    def test_exact_source_labels_and_platform_are_accepted(self):
        build.verify_image_identity(image(), BACKEND, ADMIN)

    def test_missing_and_mismatched_source_labels_are_rejected(self):
        cases = [None, {}, {**LABELS, 'io.campusqa.backend-revision': 'c' * 40},
                 {**LABELS, 'io.campusqa.admin-revision': 'd' * 40},
                 {'io.campusqa.backend-revision': BACKEND}]
        for labels in cases:
            with self.subTest(labels=labels), self.assertRaisesRegex(ValueError, 'source revision label'):
                build.verify_image_identity(image(labels), BACKEND, ADMIN)
        with self.assertRaisesRegex(ValueError, 'source revision label'):
            build.verify_image_identity({'Architecture': 'amd64', 'Os': 'linux'}, BACKEND, ADMIN)

    def test_wrong_platform_is_rejected_even_with_matching_labels(self):
        for platform in ({'Architecture': 'arm64'}, {'Os': 'windows'}):
            with self.subTest(platform=platform), self.assertRaisesRegex(ValueError, 'linux/amd64'):
                build.verify_image_identity({**image(), **platform}, BACKEND, ADMIN)

    def test_bad_labels_stop_before_release_assembly_for_both_build_modes(self):
        for skip_build in (False, True):
            with self.subTest(skip_build=skip_build), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                release = f'b{BACKEND[:8]}-a{ADMIN[:8]}-cpu-amd64'
                # Pretend source export already finished. No real repository, model,
                # image, or Docker command is read or written by this regression.
                (root / 'stage' / release).mkdir(parents=True)
                argv = ['build_release.py', '--backend', str(root / 'backend'),
                        '--admin', str(root / 'admin'), '--models', str(root / 'models'),
                        '--stage', str(root / 'stage'), '--output', str(root / 'output'),
                        '--embedding-dimension', '384'] + (['--skip-build'] if skip_build else [])
                with patch('sys.argv', argv), patch.object(build, 'output', side_effect=[
                    BACKEND, ADMIN, json.dumps([image({**LABELS, 'io.campusqa.admin-revision': 'old'})])
                ]) as output, patch.object(build, 'run') as run, patch.object(build.shutil, 'copytree') as copytree:
                    with self.assertRaisesRegex(ValueError, 'io.campusqa.admin-revision'):
                        build.main()
                    self.assertFalse((root / 'output').exists())
                    copytree.assert_not_called()
                    self.assertEqual(output.call_count, 3)
                    self.assertEqual(run.call_count, 0 if skip_build else 1)
                    if not skip_build:
                        self.assertEqual(run.call_args.args[:3], ('docker', 'buildx', 'build'))
