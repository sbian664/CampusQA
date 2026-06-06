"""
向量存储抽象层 — 支持 Chroma / Faiss 多后端
"""
import os
import pickle
from abc import ABC, abstractmethod
from typing import List, Dict, Optional

import numpy as np

from config import CHROMA_DB_PATH, CHROMA_COLLECTION, FAISS_INDEX_PATH


class VectorStore(ABC):
    """向量存储抽象基类"""

    @abstractmethod
    def add(self, ids: List[str], documents: List[str],
            metadatas: List[Dict], embeddings: List[List[float]]) -> None:
        """添加向量"""

    @abstractmethod
    def search(self, query_embedding: List[float], top_k: int) -> Dict:
        """
        搜索
        Returns: {"ids": [...], "documents": [...], "metadatas": [...], "distances": [...]}
        """

    @abstractmethod
    def delete(self, ids: List[str]) -> None:
        """按 ID 删除向量"""

    @abstractmethod
    def clear(self) -> None:
        """清空所有向量"""

    @abstractmethod
    def count(self) -> int:
        """向量总数"""


# ==================== Chroma 后端 ====================

class ChromaStore(VectorStore):
    """基于 Chroma (SQLite) 的向量存储"""

    def __init__(self):
        import chromadb
        self.client = chromadb.PersistentClient(path=CHROMA_DB_PATH)
        self.collection = self.client.get_or_create_collection(
            name=CHROMA_COLLECTION,
            metadata={"hnsw:space": "l2"},
        )
        print("✓ Chroma 向量存储已初始化")

    def add(self, ids, documents, metadatas, embeddings):
        self.collection.add(
            ids=ids,
            documents=documents,
            metadatas=metadatas,
            embeddings=embeddings,
        )

    def search(self, query_embedding, top_k):
        return self.collection.query(
            query_embeddings=[query_embedding],
            n_results=top_k,
        )

    def delete(self, ids):
        if ids:
            self.collection.delete(ids=ids)

    def clear(self):
        try:
            self.collection.delete(where={})
        except Exception:
            pass

    def count(self):
        return self.collection.count()


# ==================== Faiss 后端 ====================

class FaissStore(VectorStore):
    """基于 Faiss (IndexFlatL2) 的内存向量存储，支持持久化"""

    def __init__(self, dimension: int):
        import faiss
        self.dimension = dimension
        self.index = faiss.IndexFlatL2(dimension)
        self._ids: List[str] = []
        self._documents: List[str] = []
        self._metadatas: List[Dict] = []

        # 尝试从磁盘加载
        loaded = self._load()
        if loaded:
            print(f"✓ Faiss 向量存储已加载 ({self.index.ntotal} 条)")
        else:
            print(f"✓ Faiss 向量存储已初始化 (维度={dimension})")

    def add(self, ids, documents, metadatas, embeddings):
        if not embeddings:
            return
        vectors = np.array(embeddings, dtype=np.float32)
        self.index.add(vectors)
        self._ids.extend(ids)
        self._documents.extend(documents)
        self._metadatas.extend(metadatas)

    def search(self, query_embedding, top_k):
        if self.index.ntotal == 0:
            return {"ids": [[]], "documents": [[]], "metadatas": [[]], "distances": [[]]}

        q = np.array([query_embedding], dtype=np.float32)
        k = min(top_k, self.index.ntotal)
        distances, indices = self.index.search(q, k)

        ids = [[self._ids[i] for i in indices[0]]]
        docs = [[self._documents[i] for i in indices[0]]]
        metas = [[self._metadatas[i] for i in indices[0]]]
        dists = distances.tolist()

        return {"ids": ids, "documents": docs, "metadatas": metas, "distances": dists}

    def delete(self, ids):
        # Faiss IndexFlatL2 不支持删除单条 — 重建索引
        if not ids:
            return
        remove_set = set(ids)
        keep_indices = [i for i, _id in enumerate(self._ids) if _id not in remove_set]
        if len(keep_indices) == len(self._ids):
            return  # nothing to delete

        # 重建
        new_index = self._create_empty_index()
        new_ids, new_docs, new_metas = [], [], []
        vectors = []
        for i in keep_indices:
            # 从旧索引中取回向量
            vec = self.index.reconstruct(i)
            vectors.append(vec)
            new_ids.append(self._ids[i])
            new_docs.append(self._documents[i])
            new_metas.append(self._metadatas[i])

        self.index = new_index
        self._ids = new_ids
        self._documents = new_docs
        self._metadatas = new_metas
        if vectors:
            self.index.add(np.array(vectors, dtype=np.float32))

    def clear(self):
        self.index = self._create_empty_index()
        self._ids.clear()
        self._documents.clear()
        self._metadatas.clear()

    def count(self):
        return self.index.ntotal

    def _create_empty_index(self):
        import faiss
        return faiss.IndexFlatL2(self.dimension)

    # ---- 持久化 ----

    def save(self):
        """保存索引到磁盘"""
        import faiss
        os.makedirs(os.path.dirname(FAISS_INDEX_PATH), exist_ok=True)
        faiss.write_index(self.index, FAISS_INDEX_PATH)
        meta_path = FAISS_INDEX_PATH + ".meta"
        with open(meta_path, "wb") as f:
            pickle.dump({
                "ids": self._ids,
                "documents": self._documents,
                "metadatas": self._metadatas,
                "dimension": self.dimension,
            }, f)
        print(f"✓ Faiss 索引已保存 ({self.index.ntotal} 条)")

    def _load(self) -> bool:
        """从磁盘加载索引"""
        import faiss
        meta_path = FAISS_INDEX_PATH + ".meta"
        if not os.path.exists(FAISS_INDEX_PATH) or not os.path.exists(meta_path):
            return False
        try:
            self.index = faiss.read_index(FAISS_INDEX_PATH)
            with open(meta_path, "rb") as f:
                data = pickle.load(f)
            self._ids = data["ids"]
            self._documents = data["documents"]
            self._metadatas = data["metadatas"]
            return True
        except Exception as e:
            print(f"⚠️  Faiss 索引加载失败: {e}，将使用空索引")
            return False


# ==================== 工厂函数 ====================

def create_vector_store(store_type: str, dimension: int = 384) -> VectorStore:
    """
    工厂函数 — 创建向量存储实例

    Args:
        store_type: "chroma" 或 "faiss"
        dimension: 向量维度（Faiss 专用）

    Returns:
        VectorStore 实例
    """
    if store_type == "faiss":
        return FaissStore(dimension)
    else:
        return ChromaStore()
