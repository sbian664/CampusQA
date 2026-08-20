"""Helpers for keeping Hugging Face models in project-local directories."""

import os
from typing import Iterable, Union


RequiredFile = Union[str, Iterable[str]]


def is_local_model_ready(model_path: str, required_files: Iterable[RequiredFile]) -> bool:
    """Return whether all required model files exist in a local directory."""
    if not os.path.isdir(model_path):
        return False

    for requirement in required_files:
        candidates = (requirement,) if isinstance(requirement, str) else tuple(requirement)
        if not any(os.path.isfile(os.path.join(model_path, candidate)) for candidate in candidates):
            return False
    return True


def download_huggingface_model(model_name: str, model_path: str) -> None:
    """Download a Hugging Face repository into a project-local directory."""
    from huggingface_hub import snapshot_download

    os.makedirs(model_path, exist_ok=True)
    snapshot_download(repo_id=model_name, local_dir=model_path)


def ensure_local_huggingface_model(
    model_name: str,
    model_path: str,
    required_files: Iterable[RequiredFile],
    auto_download: bool = True,
    label: str = "model",
) -> str:
    """Reuse a complete local model, downloading it only when it is missing."""
    model_path = os.path.abspath(os.fspath(model_path))
    if is_local_model_ready(model_path, required_files):
        print(f"[{label}] use local model: {model_path}")
        return model_path

    if not auto_download:
        raise FileNotFoundError(
            f"Local {label} model is missing or incomplete: {model_path}. "
            "Download it first or enable auto download."
        )

    print(f"[{label}] download model to: {model_path}")
    download_huggingface_model(model_name, model_path)
    if not is_local_model_ready(model_path, required_files):
        raise RuntimeError(f"Downloaded {label} model is incomplete: {model_path}")
    print(f"[{label}] model downloaded: {model_path}")
    return model_path
