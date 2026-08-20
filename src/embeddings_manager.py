"""
向量化管理器 — 支持本地模型 / OpenAI API / 兼容接口
"""
import pickle
import os
import hashlib
import threading
from abc import ABC, abstractmethod
from typing import List

from config import (
    KB_EMBEDDINGS_PROVIDER,
    EMBEDDINGS_MODEL,
    EMBEDDINGS_MODEL_PATH,
    EMBEDDINGS_AUTO_DOWNLOAD,
    EMBEDDINGS_CACHE_FILE,
    OPENAI_API_KEY,
    OPENAI_API_BASE,
    OPENAI_EMBEDDINGS_MODEL,
)


def _is_local_embedding_model_ready(model_path: str) -> bool:
    """检查本地模型是否至少包含 Sentence-Transformers 的完整入口文件。"""
    return (
        os.path.isfile(os.path.join(model_path, "modules.json"))
        and os.path.isfile(os.path.join(model_path, "1_Pooling", "config.json"))
    )


def _download_embedding_model(model_name: str, model_path: str) -> None:
    """将 Hugging Face 模型仓库下载到项目目录，而不是依赖全局缓存。"""
    from huggingface_hub import snapshot_download

    os.makedirs(model_path, exist_ok=True)
    snapshot_download(repo_id=model_name, local_dir=model_path)


def ensure_local_embedding_model(
    model_name: str = EMBEDDINGS_MODEL,
    model_path: str = EMBEDDINGS_MODEL_PATH,
    auto_download: bool = EMBEDDINGS_AUTO_DOWNLOAD,
) -> str:
    """返回可供本地加载的模型目录，缺失时只下载一次。"""
    model_path = os.path.abspath(os.fspath(model_path))
    if _is_local_embedding_model_ready(model_path):
        print(f"[embeddings] use local model: {model_path}")
        return model_path

    if not auto_download:
        raise FileNotFoundError(
            f"本地 Embeddings 模型不存在或不完整: {model_path}。"
            "请先下载模型，或设置 EMBEDDINGS_AUTO_DOWNLOAD=true。"
        )

    print(f"[embeddings] download model to: {model_path}")
    _download_embedding_model(model_name, model_path)
    if not _is_local_embedding_model_ready(model_path):
        raise RuntimeError(f"Embeddings 模型下载完成但文件不完整: {model_path}")
    print(f"[embeddings] model downloaded: {model_path}")
    return model_path


# ==================== 抽象基类 ====================

class EmbeddingsProvider(ABC):
    """向量化提供商抽象基类"""

    @abstractmethod
    def embed_text(self, text: str) -> List[float]:
        """单条向量化"""

    @abstractmethod
    def embed_batch(self, texts: List[str], batch_size: int = 32) -> List[List[float]]:
        """批量向量化"""

    @property
    @abstractmethod
    def dimension(self) -> int:
        """向量维度"""

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """提供商标识（用于缓存 key）"""


# ==================== 本地模型 ====================

class LocalEmbeddingsProvider(EmbeddingsProvider):
    """Sentence-Transformers 本地模型"""

    def __init__(self):
        from sentence_transformers import SentenceTransformer
        model_path = ensure_local_embedding_model()
        print(f"[embeddings] load local model: {model_path}")
        self.model = SentenceTransformer(model_path, local_files_only=True)
        self._dim = self.model.get_embedding_dimension()
        print(f"[embeddings] model ready, dimension: {self._dim}")

    def embed_text(self, text: str) -> List[float]:
        return self.model.encode(text, normalize_embeddings=True).tolist()

    def embed_batch(self, texts: List[str], batch_size: int = 32) -> List[List[float]]:
        return self.model.encode(texts, batch_size=batch_size, normalize_embeddings=True).tolist()

    @property
    def dimension(self) -> int:
        return self._dim

    @property
    def provider_name(self) -> str:
        return f"local:{EMBEDDINGS_MODEL}"


# ==================== OpenAI / 兼容 API ====================

class OpenAIEmbeddingsProvider(EmbeddingsProvider):
    """OpenAI 及兼容接口的 Embeddings 提供商"""

    def __init__(self, model: str = None, api_key: str = None, api_base: str = None):
        from openai import OpenAI
        self.model_name = model or OPENAI_EMBEDDINGS_MODEL
        self.client = OpenAI(
            api_key=api_key or OPENAI_API_KEY,
            base_url=api_base or OPENAI_API_BASE,
        )
        # 获取维度
        test_vec = self.embed_text("test")
        self._dim = len(test_vec)
        print(f"✓ OpenAI Embeddings 已初始化: {self.model_name} (维度={self._dim})")

    def embed_text(self, text: str) -> List[float]:
        resp = self.client.embeddings.create(model=self.model_name, input=text)
        return resp.data[0].embedding

    def embed_batch(self, texts: List[str], batch_size: int = 32) -> List[List[float]]:
        all_vectors = []
        for i in range(0, len(texts), batch_size):
            batch = texts[i:i + batch_size]
            resp = self.client.embeddings.create(model=self.model_name, input=batch)
            all_vectors.extend([d.embedding for d in resp.data])
        return all_vectors

    @property
    def dimension(self) -> int:
        return self._dim

    @property
    def provider_name(self) -> str:
        return f"openai:{self.model_name}"


# ==================== 工厂函数 ====================

def _create_provider(provider: str) -> EmbeddingsProvider:
    """创建向量化提供商实例"""
    if provider == "local":
        return LocalEmbeddingsProvider()
    elif provider in ("openai", "deepseek_api"):
        # deepseek_api 暂用 OpenAI 兼容接口（或降级到 local）
        if provider == "deepseek_api":
            print("⚠️  DeepSeek embeddings API 预留，请使用 'openai' 或 'local'")
            return LocalEmbeddingsProvider()
        return OpenAIEmbeddingsProvider()
    else:
        raise ValueError(f"不支持的向量化提供商: {provider}")


# ==================== 向量化管理器 ====================

class EmbeddingsManager:
    """
    向量化管理器 — 统一缓存 + 多提供商
    """

    def __init__(self, provider: str = None):
        """
        Args:
            provider: "local" / "openai" / "deepseek_api"
        """
        self.provider_name = provider or KB_EMBEDDINGS_PROVIDER
        self._provider = _create_provider(self.provider_name)
        self._cache_lock = threading.RLock()
        self.cache = self._load_cache()

    # ---- 公共接口 ----

    def embed_text(self, text: str) -> List[float]:
        cache_key = self._make_cache_key(text)
        with self._cache_lock:
            if cache_key in self.cache:
                return self.cache[cache_key]
        vector = self._provider.embed_text(text)
        with self._cache_lock:
            return self.cache.setdefault(cache_key, vector)

    def embed_batch(self, texts: List[str], batch_size: int = 32) -> List[List[float]]:
        vectors = [None] * len(texts)
        uncached_texts = []
        uncached_indices = []

        for i, text in enumerate(texts):
            cache_key = self._make_cache_key(text)
            with self._cache_lock:
                cached = self.cache.get(cache_key)
            if cached is not None:
                vectors[i] = cached
            else:
                uncached_texts.append(text)
                uncached_indices.append(i)

        if uncached_texts:
            new_vectors = self._provider.embed_batch(uncached_texts, batch_size)
            for idx, vec in zip(uncached_indices, new_vectors):
                vectors[idx] = vec
                cache_key = self._make_cache_key(uncached_texts[uncached_indices.index(idx)])
                with self._cache_lock:
                    self.cache[cache_key] = vec

        return vectors

    def get_embedding_dimension(self) -> int:
        return self._provider.dimension

    @property
    def embed_dim(self) -> int:
        """向后兼容别名"""
        return self._provider.dimension

    # ---- 缓存 ----

    def _make_cache_key(self, text: str) -> str:
        """用 SHA256 生成缓存 key（避免 hash 碰撞）"""
        raw = f"{self._provider.provider_name}:{text}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def save_cache(self):
        try:
            os.makedirs(os.path.dirname(EMBEDDINGS_CACHE_FILE), exist_ok=True)
            with self._cache_lock:
                cache_snapshot = dict(self.cache)
            with open(EMBEDDINGS_CACHE_FILE, 'wb') as f:
                pickle.dump(cache_snapshot, f)
            print(f"✓ 向量缓存已保存: {len(cache_snapshot)} 个")
        except Exception as e:
            print(f"⚠️  保存缓存失败: {str(e)}")

    def _load_cache(self) -> dict:
        if os.path.exists(EMBEDDINGS_CACHE_FILE):
            try:
                with open(EMBEDDINGS_CACHE_FILE, 'rb') as f:
                    cache = pickle.load(f)
                print(f"✓ 加载向量缓存: {len(cache)} 个")
                return cache
            except Exception as e:
                print(f"⚠️  加载缓存失败: {str(e)}")
        return {}

    def clear_cache(self):
        with self._cache_lock:
            self.cache.clear()
        if os.path.exists(EMBEDDINGS_CACHE_FILE):
            os.remove(EMBEDDINGS_CACHE_FILE)
        print("✓ 缓存已清空")

    def __repr__(self) -> str:
        return (f"EmbeddingsManager(provider={self.provider_name}, "
                f"dim={self._provider.dimension}, cache={len(self.cache)})")
