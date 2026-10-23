"""Shared upload limits and bounded blocking file work."""
import asyncio
import functools
import os
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from threading import BoundedSemaphore, local

from src.errors import FileLimitError

MAX_FILE_BYTES = 20 * 1024 * 1024
WORKERS = max(1, int(os.getenv('FILE_PROCESSING_WORKERS', '2')))
_executor = ThreadPoolExecutor(max_workers=WORKERS, thread_name_prefix='file-work')
_slots = BoundedSemaphore(WORKERS)
_local = local()


def file_operation(function):
    @functools.wraps(function)
    def wrapped(*args, **kwargs):
        if getattr(_local, 'active', False):
            return function(*args, **kwargs)
        with _slots:
            _local.active = True
            try:
                return function(*args, **kwargs)
            finally:
                _local.active = False
    return wrapped


async def run_file_work(function, *args, **kwargs):
    return await asyncio.get_running_loop().run_in_executor(
        _executor, functools.partial(file_operation(function), *args, **kwargs))


def copy_limited(source, destination, limit=None):
    limit = MAX_FILE_BYTES if limit is None else min(limit, MAX_FILE_BYTES)
    size = 0
    while True:
        chunk = source.read(min(64 * 1024, limit - size + 1))
        if not chunk:
            return size
        size += len(chunk)
        if size > limit:
            raise FileLimitError('单文件不能超过 20 MB（20971520 字节，file size limit）')
        destination.write(chunk)


def ensure_file_size(path):
    if os.path.getsize(path) > MAX_FILE_BYTES:
        raise FileLimitError('单文件不能超过 20 MB（20971520 字节，file size limit）')
