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
    CONTEXT_ENRICHMENT_ENABLED,
    CONTEXT_ENRICHMENT_TEMPLATE,
    METADATA_FILTER_FIELDS,
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

        # 如果 chunks 快照为空但存储有数据（迁移/首次场景），从存储回填
        if not self._chunk_texts and self.store.count() > 0:
            self._hydrate_chunk_texts()

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

        # 提取文档级元数据（从第一个 doc 获取）
        doc_meta = docs[0].metadata if docs else {}
        doc_type = doc_meta.get('doc_type', 'unknown')
        doc_title = doc_meta.get('title', os.path.basename(file_path))
        doc_mtime = doc_meta.get('mtime', 0)
        doc_mtime_str = doc_meta.get('mtime_str', '')

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
                'doc_total_chunks': len(chunks),
                'doc_type': doc_type,
                'title': doc_title,
                'mtime': doc_mtime,
                'mtime_str': doc_mtime_str,
                'section_path': chunk.metadata.get('section_path', ''),
                **chunk.metadata
            })

            # 获取向量（使用上下文富化后的文本做嵌入，提高语义区分度）
            chunk_meta = chunk_metadatas[-1]
            text_for_embedding = self._enrich_chunk_text(chunk.page_content, chunk_meta)
            vector = self.embeddings_manager.embed_text(text_for_embedding)
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

    def search(self, query: str, top_k: int = 3,
               filters: Optional[Dict] = None) -> List[Dict]:
        """
        纯向量搜索（支持元数据过滤）

        Args:
            query: 查询文本
            top_k: 返回结果数
            filters: 元数据过滤条件，如 {"doc_type": "markdown", "mtime_after": "2026-01-01"}
        """
        query_vector = self.embeddings_manager.embed_text(query)

        # Chroma 原生过滤
        chroma_where = self._build_chroma_where(filters) if filters else None
        raw = self.store.search(query_vector, top_k, where=chroma_where)

        results = self._format_search_results(raw)

        # Faiss 后置过滤（如果后端不支持原生过滤）
        if filters and self.store_type == 'faiss':
            results = self._apply_metadata_filter(results, filters)

        return results[:top_k]

    def hybrid_search(self, query: str, top_k: int = 3,
                      bm25_weight: float = None,
                      filters: Optional[Dict] = None) -> List[Dict]:
        """
        BM25 + 向量混合检索（支持元数据过滤）

        流程：
        1. 向量粗筛 top_k * 2 个候选
        2. 对候选做 BM25 关键词打分
        3. 融合分数排序：final = BM25_weight * log(BM25+1) + (1-BM25_weight) * Vector ** 2 * 2(缩放系数)
        4. 元数据过滤（Chromba 原生 / Faiss 后置）
        5. 返回 top_k

        Args:
            query: 查询文本
            top_k: 返回结果数
            bm25_weight: BM25 权重（0~1），默认用 config.BM25_WEIGHT
            filters: 元数据过滤条件，如 {"doc_type": "markdown", "mtime_after": "2026-01-01"}
        """
        if bm25_weight is None:
            bm25_weight = BM25_WEIGHT

        query_vector = self.embeddings_manager.embed_text(query)

        # 向量粗筛（取更多候选），Chroma 原生过滤
        candidate_k = max(top_k * 2, top_k + 5)
        chroma_where = self._build_chroma_where(filters) if filters else None
        raw = self.store.search(query_vector, candidate_k, where=chroma_where)

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
            final_score = bm25_weight * math.log(bm25_score + 1) + (1 - bm25_weight) * vec_score ** 2 * 2
            combined.append({
                'content': raw['documents'][0][i],
                'source': raw['metadatas'][0][i].get('source', 'unknown'),
                'chunk_index': raw['metadatas'][0][i].get('chunk_index', 0),
                'doc_type': raw['metadatas'][0][i].get('doc_type', 'unknown'),
                'title': raw['metadatas'][0][i].get('title', ''),
                'score': round(final_score, 4),
                'vector_score': round(vec_score, 4),
                'bm25_score': round(bm25_score, 4),
                'metadata': raw['metadatas'][0][i],
            })

        # Faiss 后置过滤
        if filters and self.store_type == 'faiss':
            combined = self._apply_metadata_filter(combined, filters)

        # 按融合分数排序
        combined.sort(key=lambda x: x['score'], reverse=True)
        return combined[:top_k]
    
    # ---- 元数据过滤 ----

    @staticmethod
    def _build_chroma_where(filters: Dict) -> Dict:
        """
        将用户友好的过滤字典转换为 Chroma where 语法

        支持：
        - 精确匹配：{"doc_type": "markdown"}  →  {"doc_type": "markdown"}
        - 时间范围：{"mtime_after": "2026-01-01"}  →  {"mtime": {"$gte": 1704067200.0}}

        Args:
            filters: {"doc_type": "markdown", "mtime_after": "2026-01-01T00:00:00"}
        """
        conditions = []

        for user_key, value in filters.items():
            if user_key not in METADATA_FILTER_FIELDS:
                continue  # 忽略未知字段

            meta_key, op_type = METADATA_FILTER_FIELDS[user_key]

            if op_type == "exact":
                conditions.append({meta_key: value})
            elif op_type == "gte":
                # 将日期字符串转为 Unix 时间戳
                ts = KnowledgeBase._parse_time_to_unix(value)
                if ts is not None:
                    conditions.append({meta_key: {"$gte": ts}})
            elif op_type == "lte":
                ts = KnowledgeBase._parse_time_to_unix(value)
                if ts is not None:
                    conditions.append({meta_key: {"$lte": ts}})

        if not conditions:
            return None
        if len(conditions) == 1:
            return conditions[0]
        return {"$and": conditions}

    @staticmethod
    def _apply_metadata_filter(results: List[Dict], filters: Dict) -> List[Dict]:
        """
        Faiss 后置元数据过滤（Python 侧）

        Args:
            results: 搜索/混合检索结果列表
            filters: 用户过滤条件
        """
        filtered = []
        for r in results:
            meta = r.get('metadata', {})
            match = True
            for user_key, value in filters.items():
                if user_key not in METADATA_FILTER_FIELDS:
                    continue
                meta_key, op_type = METADATA_FILTER_FIELDS[user_key]

                if op_type == "exact":
                    if meta.get(meta_key) != value:
                        match = False
                        break
                elif op_type == "gte":
                    ts = KnowledgeBase._parse_time_to_unix(value)
                    if ts is not None and meta.get(meta_key, 0) < ts:
                        match = False
                        break
                elif op_type == "lte":
                    ts = KnowledgeBase._parse_time_to_unix(value)
                    if ts is not None and meta.get(meta_key, 0) > ts:
                        match = False
                        break
            if match:
                filtered.append(r)
        return filtered

    @staticmethod
    def _parse_time_to_unix(time_str: str) -> Optional[float]:
        """将日期/时间字符串转换为 Unix 时间戳"""
        if not time_str:
            return None
        try:
            # 支持多种格式
            for fmt in [
                "%Y-%m-%dT%H:%M:%S",
                "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%d",
                "%Y/%m/%d",
            ]:
                try:
                    dt = datetime.strptime(time_str.strip(), fmt)
                    return dt.timestamp()
                except ValueError:
                    continue
            # 尝试 ISO 格式
            dt = datetime.fromisoformat(time_str.strip())
            return dt.timestamp()
        except Exception:
            return None

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

        # 清空向量缓存（确保用最新模型/参数重新计算）
        self.embeddings_manager.cache.clear()

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

    def _hydrate_chunk_texts(self):
        """
        从 VectorStore 回填 _chunk_texts（迁移/恢复场景）

        当 chunks.json 不存在或损坏，但向量存储中已有数据时调用。
        """
        print("🔄 从向量存储回填 chunk 文本快照...")
        try:
            data = self.store.get_all()
            ids = data.get("ids", [])
            docs = data.get("documents", [])
            for cid, doc in zip(ids, docs):
                self._chunk_texts[cid] = doc
            if self._chunk_texts:
                self._save_chunk_texts()
                print(f"✓ 回填完成: {len(self._chunk_texts)} 个 chunk")
        except Exception as e:
            print(f"⚠️  回填 chunk 快照失败: {e}")
    
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
                    'doc_type': meta.get('doc_type', 'unknown'),
                    'title': meta.get('title', ''),
                    'score': self._distance_to_score(dist),
                    'metadata': meta,
                })
        return formatted

    @staticmethod
    def _distance_to_score(distance: float) -> float:
        """L2 距离 → 相似度分数 (0~1)"""
        return round(1 - (distance / 2), 4)

    @staticmethod
    def _enrich_chunk_text(chunk_text: str, metadata: Dict) -> str:
        """
        为分块文本添加文档/章节上下文前缀（用于嵌入向量化）

        效果：相同术语在不同文档中的分块获得差异化向量
        存储原文不变，仅嵌入时使用富化版本

        Args:
            chunk_text: 原始分块文本
            metadata: 分块元数据（含 title, section_path 等）

        Returns:
            带上下文前缀的文本（如果 CONTEXT_ENRICHMENT_ENABLED=True）
            否则返回原文
        """
        if not CONTEXT_ENRICHMENT_ENABLED:
            return chunk_text

        title = metadata.get('title', '')
        section_path = metadata.get('section_path', '')

        # 如果没有任何上下文信息，直接返回原文
        if not title and not section_path:
            return chunk_text

        return CONTEXT_ENRICHMENT_TEMPLATE.format(
            title=title,
            section_path=section_path or '',
            chunk_text=chunk_text,
        )

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
        """中文+英文混合分词 — 英文按单词，中文按字符 bigram"""
        text_lower = text.lower()
        tokens = []

        # 英文单词
        tokens.extend(re.findall(r'[a-zA-Z]+', text_lower))

        # 中文字符 bigram（滑动窗口，解决贪婪匹配无法命中的问题）
        chinese_chars = re.findall(r'[\u4e00-\u9fff]', text_lower)
        for i in range(len(chinese_chars) - 1):
            tokens.append(chinese_chars[i] + chinese_chars[i + 1])

        return tokens if tokens else text_lower.split()

    # ---- 序列化 ----

    def __repr__(self) -> str:
        stats = self.get_statistics()
        return (f"KnowledgeBase(files={stats['total_files']}, "
                f"chunks={stats['total_chunks']}, "
                f"size={stats['total_size_mb']}MB, "
                f"store={self.store_type})")
