"""Export a local CrossEncoder to FP32 + INT8 ONNX outside the serving process.

Never overwrites the source or an existing destination. No Hub uploads/downloads.
Use the target CPU's profile (for example avx2), not necessarily the build host's.
"""
from __future__ import annotations

import argparse
import gc
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
from importlib.metadata import version

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for part in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(part)
    return h.hexdigest()


def export_model(source: Path, destination: Path, profile: str, max_length: int = 512) -> dict:
    source, destination = source.resolve(), destination.resolve()
    if not (source / "config.json").is_file():
        raise ValueError("--model-path must be a complete local CrossEncoder directory")
    if destination.exists() or source == destination or source in destination.parents:
        raise ValueError("--output must be a new directory outside the source model")
    if profile not in {"arm64", "avx2", "avx512", "avx512_vnni"}:
        raise ValueError("Unsupported quantization profile")

    # Heavy optional dependencies are imported only for an explicit export.
    import numpy as np
    from sentence_transformers import CrossEncoder, export_optimized_onnx_model
    from src.inference_runtime import configure_torch_threads
    from src.reranker_backend import onnx_session_options
    from config import INFERENCE_CPU_THREADS
    configure_torch_threads()
    options = onnx_session_options(INFERENCE_CPU_THREADS)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=".reranker-export-", dir=destination.parent))
    smoke_pairs = [
        ("What are the course prerequisites?", "Prerequisites: Calculus and linear algebra."),
        ("What are the course prerequisites?", "The sports center opens at eight."),
        ("这门课程的先修要求是什么？", "先修要求：微积分和线性代数。"),
    ]
    try:
        baseline = CrossEncoder(str(source), device="cpu", max_length=max_length, local_files_only=True)
        reference = np.asarray(baseline.predict(smoke_pairs, show_progress_bar=False), dtype=float)
        del baseline
        gc.collect()
        model = CrossEncoder(
            str(source), backend="onnx", device="cpu", max_length=max_length,
            local_files_only=True,
            model_kwargs={"export": True, "provider": "CPUExecutionProvider", "session_options": options},
        )
        model.save_pretrained(str(temporary))
        # Fuse the Transformer graph before INT8 (O2, no approximate GELU/O3).
        export_optimized_onnx_model(model, optimization_config="O2", model_name_or_path=str(temporary))
        del model
        gc.collect()
        model = CrossEncoder(
            str(temporary), backend="onnx", device="cpu", max_length=max_length, local_files_only=True,
            model_kwargs={"file_name": "onnx/model_O2.onnx", "export": False,
                          "provider": "CPUExecutionProvider", "session_options": options},
        )
        float_scores = np.asarray(model.predict(smoke_pairs, show_progress_bar=False), dtype=float)
        if not np.allclose(reference, float_scores, rtol=1e-3, atol=1e-3):
            raise RuntimeError("FP32 ONNX export differs materially from the PyTorch smoke reference")
        # Optimum's wrapper drops type information for some fused O2 outputs.
        # This graph was explicitly exported as FP32; tell ORT the type of
        # unannotated floating outputs rather than silently skipping fusion.
        import onnx
        from optimum.onnxruntime.configuration import AutoQuantizationConfig
        from onnxruntime.quantization import quantize_dynamic
        quantization = getattr(AutoQuantizationConfig, profile)(is_static=False)
        quantize_dynamic(
            str(temporary / "onnx/model_O2.onnx"),
            str(temporary / f"onnx/model_qint8_{profile}.onnx"),
            weight_type=quantization.weights_dtype,
            per_channel=quantization.per_channel, reduce_range=quantization.reduce_range,
            op_types_to_quantize=quantization.operators_to_quantize,
            extra_options={"DefaultTensorType": onnx.TensorProto.FLOAT,
                           "MatMulConstBOnly": True,
                           "WeightSymmetric": quantization.weights_symmetric,
                           "ActivationSymmetric": quantization.activations_symmetric},
        )
        del model
        gc.collect()
        graphs = list(temporary.rglob(f"model_qint8_{profile}.onnx"))
        float_graphs = list(temporary.rglob("model.onnx"))
        if len(graphs) != 1 or len(float_graphs) != 1:
            raise RuntimeError("Unexpected exported graph layout")
        graph_name = graphs[0].relative_to(temporary).as_posix()
        quantized = CrossEncoder(
            str(temporary), backend="onnx", device="cpu", max_length=max_length, local_files_only=True,
            model_kwargs={"file_name": graph_name, "export": False,
                          "provider": "CPUExecutionProvider", "session_options": options},
        )
        quantized_scores = np.asarray(quantized.predict(smoke_pairs, show_progress_bar=False), dtype=float)
        if not np.isfinite(quantized_scores).all() or quantized_scores.shape != reference.shape:
            raise RuntimeError("INT8 smoke prediction returned invalid scores")
        del quantized
        gc.collect()
        files = [{"path": p.relative_to(temporary).as_posix(), "bytes": p.stat().st_size,
                  "sha256": sha256(p)} for p in sorted(temporary.rglob("*")) if p.is_file()]
        manifest = {
            "format_version": 1, "profile": profile, "max_length": max_length,
            "source_config_sha256": sha256(source / "config.json"),
            "fp32_file": float_graphs[0].relative_to(temporary).as_posix(),
            "optimized_fp32_file": "onnx/model_O2.onnx", "optimization": "O2",
            "int8_file": graph_name, "files": files,
            "versions": {p: version(p) for p in ("torch", "transformers", "sentence-transformers", "onnx", "onnxruntime")},
            "smoke_only_not_quality_evaluation": True,
            "smoke_max_fp32_delta": float(np.max(np.abs(reference - float_scores))),
            "smoke_max_int8_delta": float(np.max(np.abs(reference - quantized_scores))),
        }
        (temporary / "reranker_export.json").write_text(json.dumps(manifest, indent=2) + "\n")
        temporary.rename(destination)
        return manifest
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-path", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--profile", choices=["arm64", "avx2", "avx512", "avx512_vnni"], required=True)
    parser.add_argument("--threads", type=int, default=2)
    parser.add_argument("--max-length", type=int, default=512)
    args = parser.parse_args()
    if args.threads < 1 or args.max_length < 1:
        parser.error("threads/max-length must be positive")
    os.environ["INFERENCE_CPU_THREADS"] = str(args.threads)
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    manifest = export_model(args.model_path, args.output, args.profile, args.max_length)
    print(json.dumps({"output": str(args.output), "int8_file": manifest["int8_file"],
                      "smoke_max_int8_delta": manifest["smoke_max_int8_delta"]}, indent=2))


if __name__ == "__main__":
    main()
