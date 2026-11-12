"""Offline runtime checks and stopped-data tools. Python standard library only."""
import argparse
import base64
import binascii
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile
from contextlib import contextmanager, closing


def digest(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


def child(root, name):
    root = Path(root).resolve()
    path = root / name
    if Path(name).is_absolute() or '..' in Path(name).parts or path.resolve() == root or root not in path.resolve().parents:
        raise ValueError(f'Unsafe relative path: {name}')
    if path.is_symlink():
        raise ValueError(f'Symbolic links are not permitted: {name}')
    return path


def atomic_json(path, value):
    path = Path(path)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix='.' + path.name)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def verify_models(root):
    root = Path(root)
    manifest = json.loads((root / 'manifest.json').read_text())
    records = manifest['files']
    if not records or {item['directory'] for item in manifest['models']} != {'embedding', 'reranker'}:
        raise ValueError('Model manifest must contain embedding and reranker')
    seen = set()
    for item in records:
        name = item['path']
        if name in seen:
            raise ValueError('Duplicate model manifest path')
        seen.add(name)
        path = child(root, name)
        if not path.is_file() or path.stat().st_size != item['size'] or digest(path) != item['sha256']:
            raise ValueError(f'Missing or corrupt model file: {name}')
    for directory in ('embedding', 'reranker'):
        extensions = ('.safetensors', '.bin', '.onnx') if directory == 'reranker' else ('.safetensors', '.bin')
        if not any(name.startswith(directory + '/') and name.endswith(extensions) for name in seen):
            raise ValueError(f'Missing model weights: {directory}')
    return {'verified_files': len(records), 'model_bytes': sum(item['size'] for item in records)}


def verify_reranker_selection(root):
    """Fail before serving if the configured graph is absent/unmanifested."""
    root = Path(root)
    manifest = json.loads((root / 'manifest.json').read_text())
    files = {item['path'] for item in manifest['files']}
    backend = os.getenv('RERANKER_BACKEND', 'torch').strip().lower()
    if backend == 'onnx':
        name = os.getenv('RERANKER_ONNX_FILE', 'onnx/model.onnx').strip()
        relative = Path(name)
        if relative.is_absolute() or '..' in relative.parts or relative.suffix != '.onnx':
            raise ValueError('Invalid RERANKER_ONNX_FILE')
        selected = 'reranker/' + relative.as_posix()
        if selected not in files or not child(root, selected).is_file():
            raise ValueError('Configured ONNX reranker must be present in the verified model manifest')
        if 'reranker/config.json' not in files:
            raise ValueError('ONNX reranker config.json must be included in the model manifest')
    elif backend == 'torch':
        if not any(name.startswith('reranker/') and name.endswith(('.safetensors', '.bin')) for name in files):
            raise ValueError('PyTorch reranker weights are missing; select the ONNX backend or restore FP32 weights')
    else:
        raise ValueError('RERANKER_BACKEND must be torch or onnx')


@contextmanager
def data_lock(root):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    stream = (root / '.instance.lock').open('a+')
    try:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('Data is in use: stop the application before another instance or migration')
        yield stream
    finally:
        stream.close()


def verify_identity_config():
    # Credentials are provided by the operator, never generated or copied from a release.
    for name in ('WECHAT_APP_ID', 'WECHAT_APP_SECRET'):
        value = os.getenv(name, '').strip()
        if not value or value.startswith(('your_', 'replace-')):
            raise ValueError(f'Configure {name} in config/app.env before starting')
    key = os.getenv('USER_LLM_ENCRYPTION_KEY', '').strip()
    try:
        if len(base64.b64decode(key, altchars=b'-_', validate=True)) != 32:
            raise ValueError('incorrect key length')
    except (ValueError, binascii.Error):
        raise ValueError('Configure a valid USER_LLM_ENCRYPTION_KEY; retain the existing key during migration') from None


def preflight(data, models):
    result = verify_models(models)
    verify_reranker_selection(models)
    verify_identity_config()
    for directory in ('documents', 'submissions', 'cache', 'tmp', 'model-cache'):
        path = Path(data) / directory
        path.mkdir(parents=True, exist_ok=True)
        if not os.access(path, os.R_OK | os.W_OK | os.X_OK):
            raise ValueError(f'Data directory is not writable by UID {os.getuid()}: {path}')
    fd, test = tempfile.mkstemp(dir=data)
    os.close(fd)
    Path(test).unlink()
    saved = Path(data) / 'user_llm_config.json'
    config = json.loads(saved.read_text()) if saved.exists() else {}
    provider = config.get('provider') or os.getenv('LLM_PROVIDER', 'deepseek')
    key = config.get('api_key') or os.getenv('DEEPSEEK_API_KEY', '')
    if provider != 'local' and (not key or key.startswith(('your_', 'replace-'))):
        raise ValueError('Configure a provider API key in config/app.env before starting')
    db = Path(data) / 'admin_control.db'
    has_admin = False
    if db.exists():
        if not os.access(db, os.R_OK | os.W_OK):
            raise ValueError(f'Existing database must be writable by UID {os.getuid()}: {db}')
        with closing(sqlite3.connect(f'file:{db}?mode=ro', uri=True)) as connection:
            try:
                has_admin = bool(connection.execute('SELECT COUNT(*) FROM admin_users').fetchone()[0])
            except sqlite3.OperationalError:
                pass
    if not has_admin and not os.getenv('ADMIN_INITIAL_PASSWORD') and not os.getenv('ADMIN_PASSWORD_HASH'):
        raise ValueError('Configure ADMIN_INITIAL_PASSWORD for first installation')
    return result


def require_legacy_migration(data):
    """Fail closed: the legacy mapper cannot safely rewrite managed generations."""
    versions = Path(data) / 'cache/index_versions'
    if versions.is_symlink():
        raise ValueError('Managed index migration is unsupported: index_versions is a symlink')
    if not versions.exists():
        return
    if not versions.is_dir():
        raise ValueError('Managed index migration is unsupported: invalid index_versions')
    if any(path.name != 'registry.json' for path in versions.iterdir()):
        raise ValueError('Managed index migration is unsupported: preserve all versions and migrate a backup separately')
    registry_path = versions / 'registry.json'
    if registry_path.is_symlink():
        raise ValueError('Managed index migration is unsupported: registry is a symlink')
    if not registry_path.exists():
        return
    try:
        registry = json.loads(registry_path.read_text())
    except (ValueError, OSError):
        raise ValueError('Managed index migration is unsupported: unreadable registry') from None
    if (not isinstance(registry, dict) or registry.get('active') != 'legacy'
            or registry.get('previous') is not None or registry.get('building') is not None):
        raise ValueError('Managed index migration is unsupported: active, previous or building version is not legacy-only')


def migration_plan(data, old_root):
    data = Path(data).resolve()
    require_legacy_migration(data)
    documents = data / 'documents'
    old_root = Path(old_root)
    if not old_root.is_absolute():
        raise ValueError('--old-root must be the absolute previous documents directory')
    if list((data / 'tmp').glob('campusqa_rollback_*')):
        raise ValueError('Unresolved rollback backups found: repair them before migration')
    meta = data / 'cache/kb_metadata.json'
    original = json.loads(meta.read_text()) if meta.exists() else {}
    updated, mapping = {}, []
    for old, item in original.items():
        try:
            relative = Path(old).relative_to(old_root)
        except ValueError:
            raise ValueError(f'Metadata path outside old document root: {old}')
        destination = child(documents, relative.as_posix())
        if not destination.is_file():
            raise ValueError(f'Missing source file: {relative}')
        new = str(destination)
        if new in updated:
            raise ValueError(f'Conflicting migration target: {new}')
        record = dict(item)
        # Preserve the identity used by admin links, including legacy documents.
        record.setdefault('document_id', hashlib.sha256(relative.as_posix().encode()).hexdigest()[:24])
        if 'source' in record:
            if record['source'] != old:
                raise ValueError(f'Unexpected source mapping in metadata: {old}')
            record['source'] = new
        updated[new] = record
        mapping.append({'old': old, 'new': new, 'document_id': record['document_id']})
    return meta, original, updated, mapping


def migrate(data, old_root, apply=False):
    data = Path(data).resolve()
    with data_lock(data):
        if (data / 'cache/kb_metadata.before-container.json').exists():
            raise ValueError('Migration already applied; use the previous report or a fresh backup')
        meta, original, updated, mapping = migration_plan(data, old_root)
        result = {'apply': apply, 'documents': mapping, 'index_state': 'repair_required'}
        if apply:
            meta.parent.mkdir(parents=True, exist_ok=True)
            backup = meta.with_name('kb_metadata.before-container.json')
            if backup.exists():
                raise ValueError('Migration already applied; use the previous report or a fresh backup')
            atomic_json(backup, original)
            # Close the search gate BEFORE changing any source path.
            atomic_json(meta.with_name('index_state.json'), {
                'status': 'repair_required', 'reason': 'Container path migration: complete rebuild required',
            })
            atomic_json(meta, updated)
            atomic_json(data / 'migration-report.json', result)
        return result


def backup_data(data, destination):
    data, destination = Path(data).resolve(), Path(destination).resolve()
    if destination == data or data in destination.parents:
        raise ValueError('Backup must be outside data')
    if destination.exists():
        raise ValueError('Backup destination already exists')
    with data_lock(data):
        shutil.copytree(data, destination, ignore=shutil.ignore_patterns('.instance.lock'))
    return {'backup': str(destination)}


def restore_data(data, source):
    data, source = Path(data).resolve(), Path(source).resolve()
    if source == data or source in data.parents or data in source.parents:
        raise ValueError('Restore source and destination must be disjoint')
    with data_lock(data):
        if any(p.name != '.instance.lock' for p in data.iterdir()):
            raise ValueError('Restore only accepts an empty destination; preserve the old data directory first')
        for path in source.iterdir():
            if path.name == '.instance.lock':
                continue
            if path.is_symlink():
                raise ValueError('Unexpected backup symlink')
            if path.is_dir():
                shutil.copytree(path, data / path.name, symlinks=False)
            else:
                shutil.copy2(path, data / path.name)
    return {'restored_from': str(source)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['serve', 'check', 'models', 'migrate', 'backup', 'restore'])
    parser.add_argument('--data', default='/app/data')
    parser.add_argument('--models', default='/models')
    parser.add_argument('--old-root')
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--destination')
    parser.add_argument('--source')
    args = parser.parse_args()
    try:
        if args.command == 'models':
            result = verify_models(args.models)
        elif args.command == 'migrate':
            if not args.old_root:
                parser.error('--old-root is required')
            result = migrate(args.data, args.old_root, args.apply)
        elif args.command == 'backup':
            if not args.destination:
                parser.error('--destination is required')
            result = backup_data(args.data, args.destination)
        elif args.command == 'restore':
            if not args.source:
                parser.error('--source is required')
            result = restore_data(args.data, args.source)
        else:
            with data_lock(args.data) as lock:
                result = preflight(args.data, args.models)
                if args.command == 'serve':
                    print(json.dumps(result), flush=True)
                    os.set_inheritable(lock.fileno(), True)
                    os.execvp('uvicorn', ['uvicorn', 'server:app', '--host', '0.0.0.0', '--port', '8000',
                                         '--workers', '1', '--timeout-graceful-shutdown', '150'])
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except Exception as error:
        print(f'Offline preflight failed: {error}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
