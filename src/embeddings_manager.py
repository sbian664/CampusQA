"""
向量化管理器 — 支持本地模型 / OpenAI API / 兼容接口
"""
import pickle
import os
import hashlib
from abc import ABC, abstractmethod
from typing import List

from config import (
    KB_EMBEDDINGS_PROVIDER,
    EMBEDDINGS_MODEL,
    EMBEDDINGS_CACHE_FILE,
    OPENAI_API_KEY,
    OPENAI_API_BASE,
    OPENAI_EMBEDDINGS_MODEL,
)


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
        print(f"📥 加载本地向量模型: {EMBEDDINGS_MODEL}")
        self.model = SentenceTransformer(EMBEDDINGS_MODEL)
        self._dim = self.model.get_embedding_dimension()
        print(f"✓ 模型已加载，向量维度: {self._dim}")

    def embed_text(self, text: str) -> List[float]:
        return self.model.encode(text).tolist()

    def embed_batch(self, texts: List[str], batch_size: int = 32) -> List[List[float]]:
        return self.model.encode(texts, batch_size=batch_size).tolist()

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
        self.cache = self._load_cache()

    # ---- 公共接口 ----

    def embed_text(self, text: str) -> List[float]:
        cache_key = self._make_cache_key(text)
        if cache_key in self.cache:
            return self.cache[cache_key]
        vector = self._provider.embed_text(text)
        self.cache[cache_key] = vector
        return vector

    def embed_batch(self, texts: List[str], batch_size: int = 32) -> List[List[float]]:
        vectors = [None] * len(texts)
        uncached_texts = []
        uncached_indices = []

        for i, text in enumerate(texts):
            cache_key = self._make_cache_key(text)
            if cache_key in self.cache:
                vectors[i] = self.cache[cache_key]
            else:
                uncached_texts.append(text)
                uncached_indices.append(i)

        if uncached_texts:
            new_vectors = self._provider.embed_batch(uncached_texts, batch_size)
            for idx, vec in zip(uncached_indices, new_vectors):
                vectors[idx] = vec
                cache_key = self._make_cache_key(uncached_texts[uncached_indices.index(idx)])
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
            with open(EMBEDDINGS_CACHE_FILE, 'wb') as f:
                pickle.dump(self.cache, f)
            print(f"✓ 向量缓存已保存: {len(self.cache)} 个")
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
        self.cache.clear()
        if os.path.exists(EMBEDDINGS_CACHE_FILE):
            os.remove(EMBEDDINGS_CACHE_FILE)
        print("✓ 缓存已清空")

    def __repr__(self) -> str:
        return (f"EmbeddingsManager(provider={self.provider_name}, "
                f"dim={self._provider.dimension}, cache={len(self.cache)})")
