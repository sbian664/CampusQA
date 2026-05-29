"""
向量化管理器 - 支持本地模型和 API
"""
import pickle
import os
from typing import List, Union
from config import (
    KB_EMBEDDINGS_PROVIDER,
    EMBEDDINGS_MODEL,
    EMBEDDINGS_CACHE_FILE,
    DEEPSEEK_API_BASE,
    DEEPSEEK_API_KEY
)


class EmbeddingsManager:
    """
    向量化管理器 - 支持多个提供商（本地/API）
    """
    
    def __init__(self, provider: str = None):
        """
        初始化向量化管理器
        
        Args:
            provider (str): 提供商
                           - "local": 本地模型（默认）
                           - "deepseek_api": DeepSeek API
                           - "openai": OpenAI API
        
        Examples:
            >>> em = EmbeddingsManager()
            >>> vector = em.embed_text("你好")
            >>> len(vector)
            384  # MiniLM 的向量维度
        """
        self.provider = provider or KB_EMBEDDINGS_PROVIDER
        self.cache = self._load_cache()
        
        if self.provider == "local":
            self._init_local()
        elif self.provider == "deepseek_api":
            self._init_deepseek()
        else:
            raise ValueError(f"不支持的提供商: {self.provider}")
    
    def _init_local(self):
        """初始化本地模型"""
        try:
            from sentence_transformers import SentenceTransformer
            print(f"📥 加载本地向量模型: {EMBEDDINGS_MODEL}")
            self.embedder = SentenceTransformer(EMBEDDINGS_MODEL)
            self.embed_dim = self.embedder.get_sentence_embedding_dimension()
            print(f"✓ 模型已加载，向量维度: {self.embed_dim}")
        except Exception as e:
            raise Exception(f"加载本地模型失败: {str(e)}")
    
    def _init_deepseek(self):
        """初始化 DeepSeek API"""
        if not DEEPSEEK_API_KEY:
            raise ValueError("DEEPSEEK_API_KEY 未设置")
        
        # DeepSeek 暂无官方 embeddings API，这里预留接口
        print("⚠️  DeepSeek embeddings API 预留，暂使用本地模型作为后备")
        self._init_local()
    
    def embed_text(self, text: str) -> List[float]:
        """
        单条文本向量化
        
        Args:
            text (str): 输入文本
        
        Returns:
            List[float]: 向量
        
        Examples:
            >>> em = EmbeddingsManager()
            >>> vec = em.embed_text("机器学习")
            >>> print(vec[:5])  # 打印前5个值
        """
        # 检查缓存
        cache_key = hash(text) % (2**31)
        if cache_key in self.cache:
            return self.cache[cache_key]
        
        # 计算向量
        if self.provider == "local":
            vector = self.embedder.encode(text).tolist()
        else:
            vector = self.embedder.encode(text).tolist()
        
        # 缓存
        self.cache[cache_key] = vector
        
        return vector
    
    def embed_batch(self, texts: List[str], batch_size: int = 32) -> List[List[float]]:
        """
        批量向量化（带缓存）
        
        Args:
            texts (List[str]): 文本列表
            batch_size (int): 批处理大小
        
        Returns:
            List[List[float]]: 向量列表
        
        Examples:
            >>> em = EmbeddingsManager()
            >>> texts = ["文本1", "文本2", "文本3"]
            >>> vectors = em.embed_batch(texts)
            >>> len(vectors)
            3
        """
        vectors = []
        uncached_texts = []
        uncached_indices = []
        
        # 分离缓存和未缓存的文本
        for i, text in enumerate(texts):
            cache_key = hash(text) % (2**31)
            if cache_key in self.cache:
                vectors.append(self.cache[cache_key])
            else:
                uncached_texts.append(text)
                uncached_indices.append(i)
                vectors.append(None)  # 占位符
        
        # 批量计算未缓存的向量
        if uncached_texts:
            if self.provider == "local":
                new_vectors = self.embedder.encode(
                    uncached_texts,
                    batch_size=batch_size
                ).tolist()
            else:
                new_vectors = self.embedder.encode(
                    uncached_texts,
                    batch_size=batch_size
                ).tolist()
            
            # 填充向量和缓存
            for idx, new_vec in zip(uncached_indices, new_vectors):
                vectors[idx] = new_vec
                cache_key = hash(uncached_texts[uncached_indices.index(idx)]) % (2**31)
                self.cache[cache_key] = new_vec
        
        return vectors
    
    def get_embedding_dimension(self) -> int:
        """获取向量维度"""
        return self.embed_dim
    
    def save_cache(self):
        """保存向量缓存到文件"""
        try:
            os.makedirs(os.path.dirname(EMBEDDINGS_CACHE_FILE), exist_ok=True)
            with open(EMBEDDINGS_CACHE_FILE, 'wb') as f:
                pickle.dump(self.cache, f)
            print(f"✓ 向量缓存已保存: {len(self.cache)} 个")
        except Exception as e:
            print(f"⚠️  保存缓存失败: {str(e)}")
    
    def _load_cache(self) -> dict:
        """从文件加载向量缓存"""
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
        """清空缓存"""
        self.cache.clear()
        if os.path.exists(EMBEDDINGS_CACHE_FILE):
            os.remove(EMBEDDINGS_CACHE_FILE)
        print("✓ 缓存已清空")
    
    def __repr__(self) -> str:
        """字符串表示"""
        return f"EmbeddingsManager(provider={self.provider}, dim={self.embed_dim}, cache_size={len(self.cache)})"
