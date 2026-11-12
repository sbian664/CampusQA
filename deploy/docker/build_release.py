"""Build a two-repository, model-complete offline release from committed sources."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile

HERE = Path(__file__).resolve().parent


def run(*args, **kwargs):
    return subprocess.run(args, check=True, **kwargs)


def output(*args):
    return subprocess.check_output(args, text=True).strip()


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def export(repo, revision, target, paths=None):
    target.mkdir(parents=True)
    with tempfile.TemporaryFile() as stream:
        run('git', '-C', str(repo), 'archive', revision, *(paths or []), stdout=stream)
        stream.seek(0)
        with tarfile.open(fileobj=stream) as archive:
            archive.extractall(target, filter='data')


def verify_image_identity(image_info, backend, admin):
    """Never label an existing or newly built image with unverified revisions."""
    if image_info.get('Architecture') != 'amd64' or image_info.get('Os') != 'linux':
        raise ValueError('Release must contain a linux/amd64 image')
    labels = (image_info.get('Config') or {}).get('Labels') or {}
    expected = {'io.campusqa.backend-revision': backend,
                'io.campusqa.admin-revision': admin}
    for name, revision in expected.items():
        if labels.get(name) != revision:
            raise ValueError(f'Image source revision label missing or mismatched: {name}; rebuild the requested revisions')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--backend', type=Path, required=True)
    parser.add_argument('--admin', type=Path, required=True)
    parser.add_argument('--models', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--stage', type=Path, required=True)
    parser.add_argument('--backend-rev', default='HEAD')
    parser.add_argument('--admin-rev', default='HEAD')
    parser.add_argument('--skip-build', action='store_true')
    parser.add_argument('--application-only', action='store_true')
    parser.add_argument('--embedding-dimension', type=int, required=True, help='Dimension measured in the real-model acceptance run')
    args = parser.parse_args()
    backend = output('git', '-C', str(args.backend), 'rev-parse', args.backend_rev)
    admin = output('git', '-C', str(args.admin), 'rev-parse', args.admin_rev)
    release = f'b{backend[:8]}-a{admin[:8]}-cpu-amd64'
    image = f'campusqa:{release}'
    context = args.stage.resolve() / release
    if not context.exists():
        export(args.backend, backend, context / 'backend', ['config.py', 'server.py', 'src', 'scripts', 'requirements.txt', 'requirements-onnx.txt', 'requirements-dev.txt', 'tests', 'deploy/docker'])
        export(args.admin, admin, context / 'admin')
        shutil.copy2(context / 'backend/deploy/docker/.dockerignore', context / '.dockerignore')
    if not args.skip_build:
        run('docker', 'buildx', 'build', '--platform', 'linux/amd64', '--target', 'runtime', '--load',
            '--provenance=false', '--label', f'io.campusqa.backend-revision={backend}',
            '--label', f'io.campusqa.admin-revision={admin}',
            '--metadata-file', str(context / 'build-metadata.json'),
            '-t', image, '-f', str(context / 'backend/deploy/docker/Dockerfile'), str(context),
            env={**os.environ, 'BUILDX_METADATA_PROVENANCE': 'max'})
    image_info = json.loads(output('docker', 'image', 'inspect', image))[0]
    # Also check --skip-build: a tag alone does not prove its source identity.
    verify_image_identity(image_info, backend, admin)
    destination = args.output.resolve() / f'campusqa-{"update" if args.application_only else "offline"}-{release}'
    if destination.exists():
        raise ValueError('Release output already exists; choose a new output location')
    (destination / 'images').mkdir(parents=True)
    try:
        models_root = destination / 'models'
        if args.application_only:
            models_root.mkdir()
            shutil.copy2(args.models / 'models.json', models_root / 'models.json')
        else:
            shutil.copytree(args.models, models_root, symlinks=False,
                            ignore=shutil.ignore_patterns('.cache', 'manifest.json'))
        source_root = args.models.resolve()
        model_files = []
        for path in sorted(source_root.rglob('*')):
            relative = path.relative_to(source_root)
            if '.cache' in relative.parts or path.name in {'models.json', 'manifest.json'} or not path.is_file():
                continue
            if path.is_symlink():
                raise ValueError(f'Materialize model symlinks before packaging: {relative}')
            model_files.append({'path': relative.as_posix(), 'size': path.stat().st_size, 'sha256': sha(path)})
        model_manifest = {'models': json.loads((source_root / 'models.json').read_text()),
                          'embedding_dimension': args.embedding_dimension, 'files': model_files}
        (models_root / 'manifest.json').write_text(json.dumps(model_manifest, indent=2) + '\n')
        for filename in ('compose.yaml', 'app.env.example'):
            target = destination / ('config/app.env.example' if filename.endswith('example') else filename)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(context / 'backend/deploy/docker' / filename, target)
        shutil.copytree(context / 'backend/deploy/docker/release', destination / 'scripts')
        shutil.copy2(context / 'backend/deploy/docker/README.md', destination / 'README.md')
        run('docker', 'image', 'save', '-o', str(destination / 'images/app.tar'), image)
        dependencies = output('docker', 'run', '--rm', '--platform', 'linux/amd64', '--network', 'none',
                              '--entrypoint', 'cat', image, '/app/dependencies.txt')
        (destination / 'dependencies.txt').write_text(dependencies + '\n')
        runtime_bytes = int(output('docker', 'run', '--rm', '--platform', 'linux/amd64', '--network', 'none',
                                   '--user', '0', '--entrypoint', 'sh', image, '-c', 'du -sx -B1 / 2>/dev/null').split()[0])
        metadata_path = context / 'build-metadata.json'
        materials = json.loads(metadata_path.read_text()).get('buildx.build.provenance', {}).get('materials', []) if metadata_path.exists() else []
        manifest = {'release_id': release, 'backend_commit': backend, 'admin_commit': admin,
                    'platform': 'linux/amd64', 'image': image, 'image_id': image_info['Id'],
                    'image_size': image_info.get('Size'), 'model_bytes': sum(f['size'] for f in model_files),
                    'models_manifest_sha256': sha(models_root / 'manifest.json'),
                    'models_included': not args.application_only, 'runtime_rootfs_bytes': runtime_bytes,
                    'base_materials': materials}
        (destination / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
        required_kb = (runtime_bytes * 2 + sum(f['size'] for f in model_files) + (destination / 'images/app.tar').stat().st_size + 1024**3) // 1024
        (destination / 'release.env').write_text(f'CAMPUSQA_IMAGE={image}\nCAMPUSQA_IMAGE_ID={image_info["Id"]}\nCAMPUSQA_BACKEND_REV={backend}\nCAMPUSQA_ADMIN_REV={admin}\nCAMPUSQA_MODELS_INCLUDED={int(not args.application_only)}\nCAMPUSQA_REQUIRED_KB={required_kb}\n')
        lines = [f'{sha(p)}  {p.relative_to(destination).as_posix()}' for p in sorted(destination.rglob('*')) if p.is_file()]
        (destination / 'SHA256SUMS').write_text('\n'.join(lines) + '\n')
        archive_path = destination.with_suffix('.tar.gz')
        with tarfile.open(archive_path, 'w:gz', compresslevel=6) as archive:
            archive.add(destination, arcname=destination.name)
        Path(str(archive_path) + '.sha256').write_text(f'{sha(archive_path)}  {archive_path.name}\n')
        print(json.dumps({'directory': str(destination), 'archive': str(archive_path),
                          'compressed_bytes': archive_path.stat().st_size, **manifest}, indent=2))
    except Exception:
        # Keep a failed staging directory for diagnosis; it is not a usable release.
        (destination / 'INCOMPLETE').write_text('Release assembly failed; do not install.\n')
        raise


if __name__ == '__main__':
    main()
