"""Explicit, offline-only inference backend loading. Never export in a request."""
from __future__ import annotations

from pathlib import Path

from src.inference_runtime import configure_torch_threads


def validate_onnx_artifact(model_path: str, file_name: str) -> Path:
    root = Path(model_path).resolve()
    relative = Path(file_name)
    if relative.is_absolute() or ".." in relative.parts or relative.suffix != ".onnx":
        raise ValueError("RERANKER_ONNX_FILE must be a relative .onnx path inside the model directory")
    path = (root / relative).resolve()
    if root not in path.parents or not path.is_file():
        raise FileNotFoundError(f"Missing ONNX artifact: {file_name}; run scripts/export_reranker_onnx.py offline first")
    if not (root / "config.json").is_file():
        raise FileNotFoundError("ONNX model directory must include config.json and tokenizer files")
    return path


def onnx_session_options(threads: int):
    import onnxruntime as ort

    options = ort.SessionOptions()
    options.intra_op_num_threads = threads
    options.inter_op_num_threads = 1
    options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    # Avoid busy-spinning on a small shared/burstable CPU instance.
    options.add_session_config_entry("session.intra_op.allow_spinning", "0")
    options.add_session_config_entry("session.inter_op.allow_spinning", "0")
    return options


def load_backend(model_path: str, *, backend: str, file_name: str,
                 device: str, max_length: int, threads: int):
    if backend not in {"torch", "onnx"}:
        raise ValueError("RERANKER_BACKEND must be torch or onnx")
    if backend == "onnx":
        validate_onnx_artifact(model_path, file_name)
        if device not in {"cpu", "auto"}:
            raise ValueError("The ONNX reranker uses CPUExecutionProvider; set RERANKER_DEVICE=cpu")
    configure_torch_threads()
    from sentence_transformers import CrossEncoder

    common = {"max_length": max_length, "local_files_only": True}
    if backend == "torch":
        model = CrossEncoder(model_path, device=None if device == "auto" else device, **common)
    else:
        model = CrossEncoder(
            model_path, backend="onnx", device="cpu", **common,
            model_kwargs={"file_name": file_name, "export": False,
                          "provider": "CPUExecutionProvider",
                          "session_options": onnx_session_options(threads)},
        )
    model._campusqa_backend = backend
    model._campusqa_artifact = file_name if backend == "onnx" else "fp32"
    return model
