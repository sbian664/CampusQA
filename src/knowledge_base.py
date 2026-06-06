"""
知识库管理 - 文档、向量化、存储、增量更新、混合检索
"""
import json
import os
import re
import math
from typing import List, Dict, Optional
from datetime import datetime
from collections import defaultdict

from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_core.documents import Document

from config import (
    CHUNK_SIZE,
    CHUNK_OVERLAP,
    KB_METADATA_FILE,
    DOCUMENTS_DIR,
    VECTOR_STORE,
    HYBRID_SEARCH_ENABLED,
    BM25_WEIGHT,
    SEMANTIC_CHUNKING,
)
from src.document_loader import DocumentLoader
from src.embeddings_manager import EmbeddingsManager
from src.vector_store import create_vector_store, VectorStore
from src.text_chunker import SemanticChunker


class KnowledgeBase:
    """知识库管理类 - 支持增量更新"""
    
    def __init__(self, embeddings_provider: str = "local", vector_store: str = None):
        """
        初始化知识库

        Args:
            embeddings_provider: 向量化提供商 ("local" / "openai")
            vector_store: 向量存储后端 ("chroma" / "faiss")，默认读取 config
        """
        self.loader = DocumentLoader(DOCUMENTS_DIR)
        self.embeddings_manager = EmbeddingsManager(embeddings_provider)
        self.store_type = vector_store or VECTOR_STORE

        # 初始化文本分割器（语义感知）
        self.text_splitter = SemanticChunker(chunk_size=CHUNK_SIZE, chunk_overlap=CHUNK_OVERLAP)

        # 初始化向量存储
        self._init_store()

        # 加载元数据
        self.metadata = self._load_metadata()

        # BM25 索引（混合检索用）
        self._chunk_texts: Dict[str, str] = self._load_chunk_texts()
        self._bm25_corpus: List[str] = []
        self._bm25_doc_freq: Dict[str, int] = defaultdict(int)
        self._bm25_avgdl: float = 0.0
        if HYBRID_SEARCH_ENABLED and self._chunk_texts:
            self._rebuild_bm25()

    def _init_store(self):
        """初始化向量存储后端"""
        dim = self.embeddings_manager.get_embedding_dimension()
        self.store: VectorStore = create_vector_store(self.store_type, dim)
    
    def _init_chroma(self):
        """（已废弃 — 由 _init_store + VectorStore 替代）"""
        self._init_store()
    
    def load_documents_from_dir(self) -> int:
        """
        从目录加载所有文档（带增量更新检查）
        
        Returns:
            int: 新增/更新的文档数
        
        Examples:
            >>> kb = KnowledgeBase()
            >>> new_count = kb.load_documents_from_dir()
            >>> print(f"新增/更新 {new_count} 个文档")
        """
        file_list = self.loader.get_file_list()
        updated_count = 0
        
        print(f"\n📂 扫描文档目录: {len(file_list)} 个文件")
        
        for file_info in file_list:
            file_path = file_info['path']
            current_mtime = file_info['mtime']
            
            # 检查是否需要更新
            if self._should_update_file(file_path, current_mtime):
                try:
                    self._update_document(file_path)
                    updated_count += 1
                except Exception as e:
                    print(f"⚠️  处理文件失败 {file_path}: {str(e)}")
        
        # 保存元数据
        self._save_metadata()

        # 保存 chunk 文本快照（BM25 用）
        self._save_chunk_texts()

        # 持久化向量存储（Faiss 专用）
        self._save_store()

        print(f"✓ 文档加载完成: 新增/更新 {updated_count} 个\n")
        return updated_count
    
    def _should_update_file(self, file_path: str, current_mtime: float) -> bool:
        """检查文件是否需要更新"""
        if file_path not in self.metadata:
            return True
        
        stored_mtime = self.metadata[file_path].get('mtime', 0)
        return current_mtime > stored_mtime
    
    def _update_document(self, file_path: str):
        """更新单个文档"""
        # 加载文件
        docs = self.loader.load_file(file_path)

        # 如果文件已存在，先删除旧的向量
        if file_path in self.metadata:
            old_chunk_ids = self.metadata[file_path].get('chunk_ids', [])
            if old_chunk_ids:
                try:
                    self.store.delete(old_chunk_ids)
                except Exception as e:
                    print(f"⚠️  删除旧向量失败: {str(e)}")

        # 处理文档
        chunks = []
        for doc in docs:
            doc_chunks = self.text_splitter.split_documents([doc])
            chunks.extend(doc_chunks)

        # 向量化和存储
        chunk_ids = []
        chunk_texts = []
        chunk_metadatas = []
        chunk_vectors = []

        for i, chunk in enumerate(chunks):
            chunk_id = f"{os.path.basename(file_path)}_{i}"
            chunk_ids.append(chunk_id)
            chunk_texts.append(chunk.page_content)
            chunk_metadatas.append({
                'source': file_path,
                'chunk_index': i,
                **chunk.metadata
            })

            # 获取向量
            vector = self.embeddings_manager.embed_text(chunk.page_content)
            chunk_vectors.append(vector)

        # 批量存储（更高效）
        if chunk_ids:
            self.store.add(
                ids=chunk_ids,
                documents=chunk_texts,
                metadatas=chunk_metadatas,
                embeddings=chunk_vectors,
            )

        # 维护文本快照（BM25 用）
        for cid, ctext in zip(chunk_ids, chunk_texts):
            self._chunk_texts[cid] = ctext
        
        # 更新元数据
        file_stat = os.stat(file_path)
        self.metadata[file_path] = {
            'mtime': file_stat.st_mtime,
            'size': file_stat.st_size,
            'chunk_ids': chunk_ids,
            'chunk_count': len(chunk_ids),
            'updated_at': datetime.now().isoformat()
        }
        
        # 重建 BM25 索引（混合检索用）
        if HYBRID_SEARCH_ENABLED:
            self._rebuild_bm25()

        print(f"  ✓ 已处理: {os.path.basename(file_path)} ({len(chunk_ids)} chunks)")
    
    # ---- 搜索 ----

    def search(self, query: str, top_k: int = 3) -> List[Dict]:
        """纯向量搜索"""
        query_vector = self.embeddings_manager.embed_text(query)
        raw = self.store.search(query_vector, top_k)
        return self._format_search_results(raw)

    def hybrid_search(self, query: str, top_k: int = 3,
                      bm25_weight: float = None) -> List[Dict]:
        """
        BM25 + 向量混合检索

        流程：
        1. 向量粗筛 top_k * 2 个候选
        2. 对候选做 BM25 关键词打分
        3. 融合分数排序：final = BM25_weight * BM25 + (1-BM25_weight) * Vector
        4. 返回 top_k

        Args:
            query: 查询文本
            top_k: 返回结果数
            bm25_weight: BM25 权重（0~1），默认用 config.BM25_WEIGHT
        """
        if bm25_weight is None:
            bm25_weight = BM25_WEIGHT

        query_vector = self.embeddings_manager.embed_text(query)

        # 向量粗筛（取更多候选）
        candidate_k = max(top_k * 2, top_k + 5)
        raw = self.store.search(query_vector, candidate_k)

        if not raw['documents'] or not raw['documents'][0]:
            return []

        # 计算 BM25 分数
        bm25_scores = []
        for doc_text in raw['documents'][0]:
            bm25_scores.append(self._bm25_score(query, doc_text))

        # 融合分数
        combined = []
        for i in range(len(raw['documents'][0])):
            vec_score = self._distance_to_score(raw['distances'][0][i])
            bm25_score = bm25_scores[i]
            final_score = bm25_weight * bm25_score + (1 - bm25_weight) * vec_score
            combined.append({
                'content': raw['documents'][0][i],
                'source': raw['metadatas'][0][i].get('source', 'unknown'),
                'chunk_index': raw['metadatas'][0][i].get('chunk_index', 0),
                'score': round(final_score, 4),
                'vector_score': round(vec_score, 4),
                'bm25_score': round(bm25_score, 4),
                'metadata': raw['metadatas'][0][i],
            })

        # 按融合分数排序
        combined.sort(key=lambda x: x['score'], reverse=True)
        return combined[:top_k]
    
    def get_statistics(self) -> Dict:
        """获取知识库统计信息"""
        total_files = len(self.metadata)
        total_chunks = sum(m.get('chunk_count', 0) for m in self.metadata.values())
        total_size = sum(m.get('size', 0) for m in self.metadata.values())

        return {
            'total_files': total_files,
            'total_chunks': total_chunks,
            'total_size': total_size,
            'total_size_mb': round(total_size / (1024 * 1024), 2),
            'embeddings_dim': self.embeddings_manager.get_embedding_dimension(),
            'cache_size': len(self.embeddings_manager.cache),
            'store_type': self.store_type,
            'store_count': self.store.count(),
            'hybrid_search': HYBRID_SEARCH_ENABLED,
            'bm25_docs': len(self._bm25_corpus),
            'files': self.metadata,
        }
    
    def rebuild_index(self):
        """
        重建索引（清空并重新加载所有文档）
        """
        print("🔄 重建索引...")

        # 清空向量存储
        self.store.clear()

        # 清空元数据
        self.metadata.clear()

        # 重新加载
        self.load_documents_from_dir()

        # 保存缓存
        self.embeddings_manager.save_cache()

        # 持久化 Faiss 索引
        self._save_store()

        print("✓ 索引重建完成")
    
    def _load_metadata(self) -> Dict:
        """从文件加载元数据"""
        if os.path.exists(KB_METADATA_FILE):
            try:
                with open(KB_METADATA_FILE, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                print(f"⚠️  加载元数据失败: {str(e)}")
        return {}
    
    def _save_metadata(self):
        """保存元数据到文件"""
        try:
            os.makedirs(os.path.dirname(KB_METADATA_FILE), exist_ok=True)
            with open(KB_METADATA_FILE, 'w', encoding='utf-8') as f:
                json.dump(self.metadata, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"⚠️  保存元数据失败: {str(e)}")

    def _load_chunk_texts(self) -> Dict[str, str]:
        """加载 chunk 文本快照（BM25 用）"""
        chunk_file = KB_METADATA_FILE.replace('.json', '_chunks.json')
        if os.path.exists(chunk_file):
            try:
                with open(chunk_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    def _save_chunk_texts(self):
        """保存 chunk 文本快照"""
        chunk_file = KB_METADATA_FILE.replace('.json', '_chunks.json')
        try:
            with open(chunk_file, 'w', encoding='utf-8') as f:
                json.dump(self._chunk_texts, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"⚠️  保存 chunk 快照失败: {str(e)}")
    
    def _save_store(self):
        """持久化向量存储（Faiss 专用）"""
        if hasattr(self.store, 'save'):
            self.store.save()

    # ---- 辅助方法 ----

    def _format_search_results(self, raw: Dict) -> List[Dict]:
        """统一格式化搜索结果"""
        formatted = []
        if raw['documents'] and raw['documents'][0]:
            dists = raw.get('distances', [[]])
            for doc, meta, dist in zip(
                raw['documents'][0],
                raw['metadatas'][0],
                dists[0] if dists else [0] * len(raw['documents'][0]),
            ):
                formatted.append({
                    'content': doc,
                    'source': meta.get('source', 'unknown'),
                    'chunk_index': meta.get('chunk_index', 0),
                    'score': self._distance_to_score(dist),
                    'metadata': meta,
                })
        return formatted

    @staticmethod
    def _distance_to_score(distance: float) -> float:
        """L2 距离 → 相似度分数 (0~1)"""
        return round(1 - (distance / 2), 4)

    # ---- BM25 混合检索 ----

    def _rebuild_bm25(self):
        """从 _chunk_texts 快照重建 BM25 索引"""
        self._bm25_corpus = []
        self._bm25_doc_freq = defaultdict(int)
        total_len = 0

        for chunk_id, text in self._chunk_texts.items():
            self._bm25_corpus.append(text)
            tokens = set(self._tokenize(text))
            for token in tokens:
                self._bm25_doc_freq[token] += 1
            total_len += len(self._tokenize(text))

        self._bm25_avgdl = total_len / max(len(self._bm25_corpus), 1)

    def _bm25_score(self, query: str, document: str) -> float:
        """计算单文档的 BM25 分数"""
        if not self._bm25_corpus:
            return 0.0

        k1, b = 1.5, 0.75
        N = len(self._bm25_corpus)
        avgdl = self._bm25_avgdl or 1.0

        query_tokens = self._tokenize(query)
        doc_tokens = self._tokenize(document)
        doc_len = len(doc_tokens)

        score = 0.0
        for token in query_tokens:
            df = self._bm25_doc_freq.get(token, 0)
            if df == 0:
                continue
            idf = math.log((N - df + 0.5) / (df + 0.5) + 1)
            tf = doc_tokens.count(token)
            numerator = tf * (k1 + 1)
            denominator = tf + k1 * (1 - b + b * doc_len / avgdl)
            score += idf * numerator / denominator

        return score

    @staticmethod
    def _tokenize(text: str) -> List[str]:
        """中文+英文混合分词"""
        # 提取中文连续字符 + 英文单词
        tokens = re.findall(r'[\u4e00-\u9fff]+|[a-zA-Z]+', text.lower())
        return tokens if tokens else text.lower().split()

    # ---- 序列化 ----

    def __repr__(self) -> str:
        stats = self.get_statistics()
        return (f"KnowledgeBase(files={stats['total_files']}, "
                f"chunks={stats['total_chunks']}, "
                f"size={stats['total_size_mb']}MB, "
                f"store={self.store_type})")
