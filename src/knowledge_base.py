"""
知识库管理 - 文档、向量化、存储、增量更新、混合检索
"""
import json
import os
import re
import math
import concurrent.futures
from dataclasses import dataclass, field
from typing import List, Dict, Optional
from datetime import datetime
from collections import defaultdict

from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_core.documents import Document

from config import (
    CHUNK_SIZE,
    CHUNK_OVERLAP,
    KB_METADATA_FILE,
    KB_INDEX_MAX_WORKERS,
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


@dataclass(frozen=True)
class DocumentIndexFailure:
    file_path: str
    error: str


@dataclass
class DocumentIndexResult:
    indexed_paths: List[str] = field(default_factory=list)
    failures: List[DocumentIndexFailure] = field(default_factory=list)


@dataclass
class KnowledgeRetrievalResult:
    results: List[Dict] = field(default_factory=list)
    bm25_results: List[Dict] = field(default_factory=list)


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
        # In-memory chunk metadata index for BM25 filters and context expansion.
        self._chunk_metadata: Dict[str, Dict] = {}
        self._bm25_corpus: List[str] = []
        self._bm25_doc_freq: Dict[str, int] = defaultdict(int)
        self._bm25_avgdl: float = 0.0

        # 如果 chunks 快照为空但存储有数据（迁移/首次场景），从存储回填
        store_count = self.store.count()
        if store_count > 0:
            # Reconcile the snapshot with the authoritative vector store so
            # deleted chunks cannot survive in the BM25 corpus.
            self._hydrate_chunk_texts()
        else:
            self._chunk_texts.clear()
            self._chunk_metadata.clear()

        if HYBRID_SEARCH_ENABLED and self._chunk_texts:
            self._rebuild_bm25()

    def _init_store(self):
        """初始化向量存储后端"""
        dim = self.embeddings_manager.get_embedding_dimension()
        self.store: VectorStore = create_vector_store(self.store_type, dim)
    
    def _init_chroma(self):
        """（已废弃 — 由 _init_store + VectorStore 替代）"""
        self._init_store()
    
    def load_documents_from_dir(self, max_workers: int = None) -> int:
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
        max_workers = max_workers if max_workers is not None else KB_INDEX_MAX_WORKERS

        print(f"\n📂 扫描文档目录: {len(file_list)} 个文件")

        update_candidates = [
            file_info for file_info in file_list
            if self._should_update_file(file_info['path'], file_info['mtime'])
        ]

        prepared_updates = []
        if update_candidates:
            worker_count = max(1, min(int(max_workers or 1), len(update_candidates)))
            if worker_count == 1:
                for file_info in update_candidates:
                    file_path = file_info['path']
                    try:
                        prepared_updates.append(
                            self._prepare_document_update(file_path, file_info['mtime'])
                        )
                    except Exception as e:
                        print(f"⚠️  处理文件失败 {file_path}: {str(e)}")
            else:
                with concurrent.futures.ThreadPoolExecutor(max_workers=worker_count) as executor:
                    future_to_file = {
                        executor.submit(
                            self._prepare_document_update,
                            file_info['path'],
                            file_info['mtime'],
                        ): file_info['path']
                        for file_info in update_candidates
                    }
                    for future in concurrent.futures.as_completed(future_to_file):
                        file_path = future_to_file[future]
                        try:
                            prepared_updates.append(future.result())
                        except Exception as e:
                            print(f"⚠️  处理文件失败 {file_path}: {str(e)}")

        prepared_updates.sort(key=lambda item: item['file_path'])
        for prepared in prepared_updates:
            committed = self._commit_prepared_document_update(prepared)
            if committed is not False:
                updated_count += 1

        if prepared_updates and HYBRID_SEARCH_ENABLED:
            self._rebuild_bm25()

        # 保存元数据
        self._save_metadata()

        # 保存 chunk 文本快照（BM25 用）
        self._save_chunk_texts()

        # 持久化向量存储（Faiss 专用）
        self._save_store()

        print(f"✓ 文档加载完成: 新增/更新 {updated_count} 个\n")
        return updated_count

    def index_document(self, file_path: str) -> None:
        """Index and persist one existing document, raising on failure."""
        result = self.index_documents([file_path], max_workers=1)
        if result.failures:
            raise RuntimeError(result.failures[0].error)

    def index_documents(
        self, file_paths: List[str], max_workers: int = None
    ) -> DocumentIndexResult:
        """Index existing documents and report per-file failures."""
        result = self._index_candidates(
            [{'path': file_path} for file_path in file_paths],
            max_workers=max_workers,
        )
        if result.indexed_paths:
            if HYBRID_SEARCH_ENABLED:
                self._rebuild_bm25()
            self._persist_index()
        return result

    def _index_candidates(
        self, candidates: List[Dict], max_workers: int = None
    ) -> DocumentIndexResult:
        result = DocumentIndexResult()
        if not candidates:
            return result

        configured_workers = (
            max_workers if max_workers is not None else KB_INDEX_MAX_WORKERS
        )
        worker_count = max(1, min(int(configured_workers or 1), len(candidates)))
        prepared_updates = []

        def prepare(candidate: Dict) -> Dict:
            file_path = candidate['path']
            current_mtime = candidate.get('mtime')
            if current_mtime is None:
                current_mtime = os.path.getmtime(file_path)
            return self._prepare_document_update(file_path, current_mtime)

        if worker_count == 1:
            for candidate in candidates:
                file_path = candidate['path']
                try:
                    prepared_updates.append(prepare(candidate))
                except Exception as error:
                    print(f"鈿狅笍  澶勭悊鏂囦欢澶辫触 {file_path}: {str(error)}")
                    result.failures.append(DocumentIndexFailure(file_path, str(error)))
        else:
            with concurrent.futures.ThreadPoolExecutor(max_workers=worker_count) as executor:
                future_to_file = {
                    executor.submit(prepare, candidate): candidate['path']
                    for candidate in candidates
                }
                for future in concurrent.futures.as_completed(future_to_file):
                    file_path = future_to_file[future]
                    try:
                        prepared_updates.append(future.result())
                    except Exception as error:
                        print(f"鈿狅笍  澶勭悊鏂囦欢澶辫触 {file_path}: {str(error)}")
                        result.failures.append(DocumentIndexFailure(file_path, str(error)))

        prepared_updates.sort(key=lambda item: item['file_path'])
        for prepared in prepared_updates:
            try:
                self._commit_prepared_document_update(prepared)
                result.indexed_paths.append(prepared['file_path'])
            except Exception as error:
                file_path = prepared['file_path']
                print(f"鈿狅笍  提交文件失败 {file_path}: {str(error)}")
                result.failures.append(DocumentIndexFailure(file_path, str(error)))

        result.failures.sort(key=lambda item: item.file_path)
        return result

    def _persist_index(self) -> None:
        self._save_metadata()
        self._save_chunk_texts()
        self._save_store()

    def _prepare_document_update(self, file_path: str, current_mtime: float) -> Dict:
        """Prepare chunks, metadata, and embeddings without mutating the store."""
        docs = self.loader.load_file(file_path)

        chunks = []
        for doc in docs:
            doc_chunks = self.text_splitter.split_documents([doc])
            chunks.extend(doc_chunks)

        doc_meta = docs[0].metadata if docs else {}
        doc_type = doc_meta.get('doc_type', 'unknown')
        doc_title = doc_meta.get('title', os.path.basename(file_path))
        doc_mtime = doc_meta.get('mtime', current_mtime)
        doc_mtime_str = doc_meta.get('mtime_str', '')

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

            chunk_meta = chunk_metadatas[-1]
            text_for_embedding = self._enrich_chunk_text(chunk.page_content, chunk_meta)
            vector = self.embeddings_manager.embed_text(text_for_embedding)
            chunk_vectors.append(vector)

        try:
            file_stat = os.stat(file_path)
            file_mtime = file_stat.st_mtime
            file_size = file_stat.st_size
        except OSError:
            file_mtime = current_mtime
            file_size = int(doc_meta.get('size', 0) or 0)

        return {
            'file_path': file_path,
            'chunk_ids': chunk_ids,
            'chunk_texts': chunk_texts,
            'chunk_metadatas': chunk_metadatas,
            'chunk_vectors': chunk_vectors,
            'file_mtime': file_mtime,
            'file_size': file_size,
        }

    def _remove_chunk_ids(self, chunk_ids: List[str]) -> None:
        """Remove chunk text and metadata from in-memory retrieval indexes."""
        for chunk_id in chunk_ids:
            self._chunk_texts.pop(chunk_id, None)
            chunk_metadata = getattr(self, "_chunk_metadata", None)
            if chunk_metadata is not None:
                chunk_metadata.pop(chunk_id, None)

    def _delete_old_vectors(self, chunk_ids: List[str]) -> bool:
        """Delete old vectors without mutating retrieval indexes on failure."""
        try:
            self.store.delete(chunk_ids)
        except Exception as exc:
            print(f"Failed to delete old vectors: {exc}")
            return False
        return True

    def _commit_prepared_document_update(self, prepared: Dict):
        """Apply a prepared update to the vector store and local metadata."""
        file_path = prepared['file_path']
        previous_metadata = self.metadata.get(file_path)
        old_chunk_ids = (previous_metadata or {}).get('chunk_ids', [])

        # Keep enough information to restore the previous document if the
        # replacement write fails after deletion. Re-embedding happens before
        # mutating the store, so an embedding failure is also fail-safe.
        old_records = []
        for chunk_id in old_chunk_ids:
            if chunk_id not in self._chunk_texts:
                raise RuntimeError(f"缺少旧文档 chunk 快照，拒绝替换: {chunk_id}")
            old_records.append({
                'id': chunk_id,
                'document': self._chunk_texts[chunk_id],
                'metadata': dict(self._chunk_metadata.get(chunk_id, {})),
            })
        old_embeddings = [
            self.embeddings_manager.embed_text(
                self._enrich_chunk_text(item['document'], item['metadata'])
            )
            for item in old_records
        ]

        if old_chunk_ids and not self._delete_old_vectors(old_chunk_ids):
            raise RuntimeError(f"删除旧文档向量失败，未替换: {file_path}")

        chunk_ids = prepared['chunk_ids']
        chunk_texts = prepared['chunk_texts']
        try:
            if chunk_ids:
                self.store.add(
                    ids=chunk_ids,
                    documents=chunk_texts,
                    metadatas=prepared['chunk_metadatas'],
                    embeddings=prepared['chunk_vectors'],
                )
        except Exception:
            # A backend may have partially accepted the new batch. Remove it
            # before restoring the old IDs so both Chroma and Faiss converge
            # back to the pre-update state.
            try:
                self.store.delete(chunk_ids)
            except Exception as cleanup_error:
                print(f"Failed to clean up replacement vectors: {cleanup_error}")
            if old_records:
                self.store.add(
                    ids=[item['id'] for item in old_records],
                    documents=[item['document'] for item in old_records],
                    metadatas=[item['metadata'] for item in old_records],
                    embeddings=old_embeddings,
                )
            raise

        if old_chunk_ids:
            self._remove_chunk_ids(old_chunk_ids)

        for cid, ctext in zip(chunk_ids, chunk_texts):
            self._chunk_texts[cid] = ctext
        for cid, metadata in zip(chunk_ids, prepared['chunk_metadatas']):
            self._chunk_metadata[cid] = dict(metadata)

        self.metadata[file_path] = {
            'mtime': prepared['file_mtime'],
            'size': prepared['file_size'],
            'chunk_ids': chunk_ids,
            'chunk_count': len(chunk_ids),
            'updated_at': datetime.now().isoformat()
        }

        print(f"  ✓ 已处理: {os.path.basename(file_path)} ({len(chunk_ids)} chunks)")
    
        return True

    def _should_update_file(self, file_path: str, current_mtime: float) -> bool:
        """检查文件是否需要更新"""
        if file_path not in self.metadata:
            return True
        
        stored_mtime = self.metadata[file_path].get('mtime', 0)
        return current_mtime > stored_mtime
    
    def _update_document(self, file_path: str):
        """更新单个文档"""
        current_mtime = os.path.getmtime(file_path)
        prepared = self._prepare_document_update(file_path, current_mtime)
        committed = self._commit_prepared_document_update(prepared)
        if committed is False:
            raise RuntimeError(f"文档更新失败: {file_path}")
        if HYBRID_SEARCH_ENABLED:
            self._rebuild_bm25()
        return True

    def delete_document(self, file_path: str) -> bool:
        """Remove one indexed document and its source file metadata."""
        file_path = os.path.abspath(file_path)
        previous_metadata = self.metadata.get(file_path)
        if not previous_metadata:
            return False
        chunk_ids = list(previous_metadata.get("chunk_ids", []))
        if chunk_ids:
            self.store.delete(chunk_ids)
        self._remove_chunk_ids(chunk_ids)
        self.metadata.pop(file_path, None)
        if HYBRID_SEARCH_ENABLED:
            self._rebuild_bm25()
        self._persist_index()
        return True
    
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

        # 向量粗筛（扩大候选窗口，确保短词查询也能覆盖）
        candidate_k = max(top_k * 10, 50)
        chroma_where = self._build_chroma_where(filters) if filters else None
        raw = self.store.search(query_vector, candidate_k, where=chroma_where)

        if not raw['documents'] or not raw['documents'][0]:
            return []

        # 计算 BM25 分数（查询端分词：支持 "..." 字面短语）
        bm25_scores = []
        query_tokens = self._tokenize_query(query)
        for doc_text in raw['documents'][0]:
            bm25_scores.append(self._bm25_score(query, doc_text, query_tokens=query_tokens))

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

    def retrieve(
        self,
        query: str,
        top_k: int = 3,
        filters: Optional[Dict] = None,
        rescue_top_k: int = None,
    ) -> KnowledgeRetrievalResult:
        """Run hybrid retrieval and apply the global BM25 rescue policy."""
        results = self.hybrid_search(query, top_k=top_k, filters=filters)
        bm25_results = []
        if all(item.get('bm25_score', 0) == 0 for item in results):
            query_tokens = self._tokenize_query(query)
            if any(self._bm25_doc_freq.get(token, 0) > 0 for token in query_tokens):
                bm25_results = self.bm25_search(
                    query,
                    top_k=rescue_top_k or top_k,
                    filters=filters,
                )
        return KnowledgeRetrievalResult(results=results, bm25_results=bm25_results)
    
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
        self._chunk_texts.clear()
        self._chunk_metadata.clear()

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
            metadatas = data.get("metadatas", [])
            self._chunk_texts = {
                cid: doc
                for cid, doc in zip(ids, docs)
            }
            self._chunk_metadata = {
                cid: dict(metadata or {})
                for cid, metadata in zip(ids, metadatas)
            }
            if self._chunk_texts:
                self._save_chunk_texts()
                print(f"✓ 回填完成: {len(self._chunk_texts)} 个 chunk")
        except Exception as e:
            print(f"⚠️  回填 chunk 快照失败: {e}")
    
    def _hydrate_chunk_metadata(self):
        """Load chunk metadata from the vector store for local filtering/expansion."""
        try:
            data = self.store.get_all()
            ids = data.get("ids", [])
            metadatas = data.get("metadatas", [])
            self._chunk_metadata = {
                cid: dict(metadata or {})
                for cid, metadata in zip(ids, metadatas)
            }
        except Exception as e:
            print(f"Failed to hydrate chunk metadata: {e}")

    def expand_adjacent_chunks(
        self,
        results: List[Dict],
        radius: int = 1,
    ) -> List[Dict]:
        """Add nearby chunks around ranked results for answer context completeness."""
        if not results or radius <= 0:
            return results

        if not self._chunk_metadata and self.store.count() > 0:
            self._hydrate_chunk_metadata()

        chunks_by_source = defaultdict(dict)
        for chunk_id, metadata in self._chunk_metadata.items():
            source = metadata.get("source")
            index = metadata.get("chunk_index")
            if source is None or index is None:
                continue
            chunks_by_source[source][int(index)] = (chunk_id, metadata)

        expanded = list(results)
        anchor_keys = {
            (item.get("source"), item.get("chunk_index"))
            for item in results
        }
        seen = set(anchor_keys)
        for anchor in results:
            source = anchor.get("source")
            index = anchor.get("chunk_index")
            anchor_key = (source, index)

            source_chunks = chunks_by_source.get(source, {})
            for neighbor_index in range(int(index) - radius, int(index) + radius + 1):
                if neighbor_index == index or (source, neighbor_index) in seen:
                    continue
                record = source_chunks.get(neighbor_index)
                if record is None:
                    continue

                chunk_id, metadata = record
                neighbor = {
                    "content": self._chunk_texts.get(chunk_id, ""),
                    "source": source,
                    "chunk_index": neighbor_index,
                    "doc_type": metadata.get("doc_type", "unknown"),
                    "title": metadata.get("title", ""),
                    "score": anchor.get("score", 0),
                    "metadata": dict(metadata),
                    "is_context_neighbor": True,
                    "anchor_chunk": index,
                }
                if "rerank_score" in anchor:
                    neighbor["rerank_score"] = anchor["rerank_score"]
                if "rerank_rank" in anchor:
                    neighbor["rerank_rank"] = anchor["rerank_rank"]
                expanded.append(neighbor)
                seen.add((source, neighbor_index))

        return expanded

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

    # ---- 查询端分词（支持 "..." 字面保留语法） ----

    # 字面保留标记："..." 内的内容不被拆分
    QUOTED_PHRASE_PATTERN = re.compile(r'"([^"]+)"')

    @staticmethod
    def _extract_quoted_phrases(text: str):
        """
        从文本中提取 "..." 包裹的字面短语

        Returns: (清理后文本, [字面token列表])
        用法: /search "E1 L2" 办公室 → 字面 token: 'e1 l2'，其余正常分词
        """
        phrases = KnowledgeBase.QUOTED_PHRASE_PATTERN.findall(text)
        clean = KnowledgeBase.QUOTED_PHRASE_PATTERN.sub(' ', text)
        literal_tokens = [p.strip().lower() for p in phrases if p.strip()]
        return clean, literal_tokens

    @staticmethod
    def _tokenize_query(text: str) -> List[str]:
        """
        查询端分词：先提取 "..." 字面短语，再对剩余文本做标准分词

        "E1 L2" 办公室  →  ['e1 l2', '办公', '公室', '办', '公', '室']
        """
        clean_text, literal_tokens = KnowledgeBase._extract_quoted_phrases(text)
        regular_tokens = KnowledgeBase._tokenize(clean_text)
        # 字面 token 置前，BM25 匹配时优先命中
        return literal_tokens + regular_tokens

    def _bm25_score(self, query: str, document: str,
                    query_tokens: List[str] = None) -> float:
        """计算单文档的 BM25 分数"""
        if not self._bm25_corpus:
            return 0.0

        k1, b = 1.5, 0.75
        N = len(self._bm25_corpus)
        avgdl = self._bm25_avgdl or 1.0

        query_tokens = query_tokens if query_tokens is not None else self._tokenize(query)
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
            term_score = idf * numerator / denominator
            score += term_score

        return score

    def _bm25_rescue(self, query: str, top_k: int,
                     bm25_weight: float, filters: Optional[Dict]) -> List[Dict]:
        """
        BM25 救援：当向量粗筛完全没命中关键词时，全局 BM25 兜底
        """
        query_tokens = self._tokenize_query(query)
        scored = []
        for cid, doc_text in self._chunk_texts.items():
            bm = self._bm25_score(query, doc_text, query_tokens=query_tokens)
            if bm > 0:
                # 从 metadata 还原 chunk 元数据
                chunk_meta = dict(self._chunk_metadata.get(cid, {}))
                if chunk_meta:
                    source = chunk_meta.get("source", "unknown")
                    chunk_idx = int(chunk_meta.get("chunk_index", 0))
                    doc_type = chunk_meta.get("doc_type", "unknown")
                else:
                    source, chunk_idx, doc_type = self._resolve_chunk_meta(cid)

                # 后置过滤
                meta = chunk_meta or {
                    'source': source,
                    'doc_type': doc_type,
                    'mtime': self.metadata.get(source, {}).get('mtime', 0),
                }
                meta.setdefault('source', source)
                meta.setdefault('doc_type', doc_type)
                meta.setdefault('mtime', self.metadata.get(source, {}).get('mtime', 0))
                if filters and source:
                    if not self._match_meta(meta, filters):
                        continue

                scored.append({
                    'content': doc_text,
                    'source': source,
                    'chunk_index': chunk_idx,
                    'doc_type': doc_type,
                    'title': '',
                    'score': round(bm25_weight * math.log(bm + 1), 4),
                    'vector_score': None,   # 纯 BM25，无向量分
                    'bm25_score': round(bm, 4),
                    'metadata': meta,
                })

        scored.sort(key=lambda x: x['score'], reverse=True)
        return scored[:top_k]

    def bm25_search(
        self,
        query: str,
        top_k: int = 3,
        filters: Optional[Dict] = None,
    ) -> List[Dict]:
        """
        纯 BM25 关键词搜索（不做向量融合）

        供 Agent 决策层在语义搜索失效时调用
        分数与 hybrid_search 使用相同 bm25_weight，可直接对比
        """
        return self._bm25_rescue(
            query,
            top_k,
            bm25_weight=BM25_WEIGHT,
            filters=filters,
        )

    def _resolve_chunk_meta(self, chunk_id: str):
        """
        从 chunk_id 还原 source / chunk_index / doc_type

        chunk_id 格式: "faculty_profiles_002.txt_0"
        """
        # 按最后一个 _ 分割
        if '_' in chunk_id:
            *name_parts, idx_str = chunk_id.rsplit('_', 1)
            basename = '_'.join(name_parts)
            chunk_idx = int(idx_str) if idx_str.isdigit() else 0
        else:
            basename = chunk_id
            chunk_idx = 0

        # 在 metadata 中匹配完整路径
        source = basename
        doc_type = 'unknown'
        for file_path in self.metadata:
            if os.path.basename(file_path) == basename:
                source = file_path
                break

        # 从文件名推断类型
        ext = os.path.splitext(basename)[1].lower()
        type_map = {'.md': 'markdown', '.txt': 'text', '.html': 'html', '.pdf': 'pdf'}
        doc_type = type_map.get(ext, 'unknown')

        return source, chunk_idx, doc_type

    @staticmethod
    def _match_meta(meta: Dict, filters: Dict) -> bool:
        """检查元数据是否满足过滤条件"""
        for user_key, value in filters.items():
            if user_key not in METADATA_FILTER_FIELDS:
                continue
            meta_key, op_type = METADATA_FILTER_FIELDS[user_key]
            if op_type == 'exact' and meta.get(meta_key) != value:
                return False
            if op_type == 'gte':
                ts = KnowledgeBase._parse_time_to_unix(value)
                if ts is not None and meta.get(meta_key, 0) < ts:
                    return False
            if op_type == 'lte':
                ts = KnowledgeBase._parse_time_to_unix(value)
                if ts is not None and meta.get(meta_key, 0) > ts:
                    return False
        return True

    @staticmethod
    def _tokenize(text: str) -> List[str]:
        """中文+英文+数字混合分词

        策略（按序）：
        1. 特殊编码 — 字母+数字组合整体保留（E1、L2、AB12）
        2. 英文单词 — 2 字母以上的连续英文
        3. 独立数字 — 纯数字序列（房间号、步骤号）
        4. 中文 bigram — 滑动窗口字符对

        特殊编码保留规则：在检索场景中，将 "E1 L2" 这类位置编码视
        为原子 token，确保在文档与查询两端分词一致。
        """
        text_lower = text.lower()
        tokens: List[str] = []

        # ---- 第1步：特殊编码（字母+数字组合，如 E1、L2、AB12） ----
        # 用 \b 边界确保整体匹配，避免从 "textE1" 中误提取
        CODE_PATTERN = re.compile(r'\b[a-zA-Z]+\d+\b')
        code_tokens = CODE_PATTERN.findall(text_lower)
        tokens.extend(code_tokens)

        # 移除已提取的编码，避免后续步骤重复处理
        working_text = CODE_PATTERN.sub(' ', text_lower)

        # ---- 第2步：英文单词（2 字母以上，过滤掉被拆散的单字母残留） ----
        tokens.extend(re.findall(r'[a-zA-Z]{2,}', working_text))

        # ---- 第3步：独立数字 ----
        tokens.extend(re.findall(r'\d+', working_text))

        # ---- 第4步：中文 bigram ----
        chinese_chars = re.findall(r'[\u4e00-\u9fff]', working_text)
        for i in range(len(chinese_chars) - 1):
            tokens.append(chinese_chars[i] + chinese_chars[i + 1])

        return tokens if tokens else working_text.split()

    # ---- 序列化 ----

    def __repr__(self) -> str:
        stats = self.get_statistics()
        return (f"KnowledgeBase(files={stats['total_files']}, "
                f"chunks={stats['total_chunks']}, "
                f"size={stats['total_size_mb']}MB, "
                f"store={self.store_type})")
