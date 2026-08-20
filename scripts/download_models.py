"""Pre-download the local embedding and reranker models used by the backend."""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.embeddings_manager import ensure_local_embedding_model
from src.reranker import ensure_local_reranker_model


def main() -> None:
    ensure_local_embedding_model(auto_download=True)
    ensure_local_reranker_model(auto_download=True)
    print("[models] all backend models are ready")


if __name__ == "__main__":
    main()
