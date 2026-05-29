"""
知识库管理 - 文档、向量化、存储、增量更新
"""
import json
import os
from typing import List, Dict, Optional
from datetime import datetime

import chromadb
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_core.documents import Document

from config import (
    CHUNK_SIZE, 
    CHUNK_OVERLAP,
    CHROMA_DB_PATH,
    CHROMA_COLLECTION,
    KB_METADATA_FILE,
    DOCUMENTS_DIR
)
from src.document_loader import DocumentLoader
from src.embeddings_manager import EmbeddingsManager


class KnowledgeBase:
    """知识库管理类 - 支持增量更新"""
    
    def __init__(self, embeddings_provider: str = "local"):
        """
        初始化知识库
        
        Args:
            embeddings_provider (str): 向量化提供商
        
        Examples:
            >>> kb = KnowledgeBase()
            >>> kb.load_documents_from_dir()
            >>> stats = kb.get_statistics()
            >>> print(stats)
        """
        self.loader = DocumentLoader(DOCUMENTS_DIR)
        self.embeddings_manager = EmbeddingsManager(embeddings_provider)
        
        # 初始化文本分割器
        self.text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=CHUNK_SIZE,
            chunk_overlap=CHUNK_OVERLAP,
            separators=["\n\n", "\n", " ", ""]
        )
        
        # 初始化 Chroma 数据库
        self._init_chroma()
        
        # 加载元数据
        self.metadata = self._load_metadata()
    
    def _init_chroma(self):
        """初始化 Chroma 客户端和集合"""
        try:
            # 使用持久化的 SQLite 后端
            self.client = chromadb.PersistentClient(path=CHROMA_DB_PATH)
            self.collection = self.client.get_or_create_collection(
                name=CHROMA_COLLECTION,
                metadata={"hnsw:space": "l2"}  # L2 距离
            )
            print(f"✓ Chroma 数据库已初始化")
        except Exception as e:
            raise Exception(f"初始化 Chroma 失败: {str(e)}")
    
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
                    self.collection.delete(ids=old_chunk_ids)
                except Exception as e:
                    print(f"⚠️  删除旧向量失败: {str(e)}")
        
        # 处理文档
        chunks = []
        for doc in docs:
            doc_chunks = self.text_splitter.split_documents([doc])
            chunks.extend(doc_chunks)
        
        # 向量化和存储
        chunk_ids = []
        for i, chunk in enumerate(chunks):
            chunk_id = f"{os.path.basename(file_path)}_{i}"
            chunk_ids.append(chunk_id)
            
            # 获取向量
            vector = self.embeddings_manager.embed_text(chunk.page_content)
            
            # 存储到 Chroma
            self.collection.add(
                ids=[chunk_id],
                documents=[chunk.page_content],
                metadatas=[{
                    'source': file_path,
                    'chunk_index': i,
                    **chunk.metadata
                }],
                embeddings=[vector]
            )
        
        # 更新元数据
        file_stat = os.stat(file_path)
        self.metadata[file_path] = {
            'mtime': file_stat.st_mtime,
            'size': file_stat.st_size,
            'chunk_ids': chunk_ids,
            'chunk_count': len(chunk_ids),
            'updated_at': datetime.now().isoformat()
        }
        
        print(f"  ✓ 已处理: {os.path.basename(file_path)} ({len(chunk_ids)} chunks)")
    
    def search(self, query: str, top_k: int = 3) -> List[Dict]:
        """
        搜索相关文档块
        
        Args:
            query (str): 查询文本
            top_k (int): 返回结果数
        
        Returns:
            List[Dict]: 搜索结果
                       [{"content": "...", "source": "...", "score": 0.9}, ...]
        
        Examples:
            >>> kb = KnowledgeBase()
            >>> results = kb.search("机器学习", top_k=3)
            >>> for r in results:
            ...     print(f"{r['source']}: {r['content'][:50]}...")
        """
        # 向量化查询
        query_vector = self.embeddings_manager.embed_text(query)
        
        # 在 Chroma 中搜索
        results = self.collection.query(
            query_embeddings=[query_vector],
            n_results=top_k
        )
        
        # 格式化结果
        formatted_results = []
        if results['documents'] and results['documents'][0]:
            for doc, metadata, distance in zip(
                results['documents'][0],
                results['metadatas'][0],
                results['distances'][0]
            ):
                formatted_results.append({
                    'content': doc,
                    'source': metadata.get('source', 'unknown'),
                    'chunk_index': metadata.get('chunk_index', 0),
                    'score': 1 - (distance / 2),  # 转换为相似度分数
                    'metadata': metadata
                })
        
        return formatted_results
    
    def get_statistics(self) -> Dict:
        """
        获取知识库统计信息
        
        Returns:
            Dict: 统计信息
        
        Examples:
            >>> kb = KnowledgeBase()
            >>> stats = kb.get_statistics()
            >>> print(f"总文档数: {stats['total_files']}")
            >>> print(f"总块数: {stats['total_chunks']}")
        """
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
            'files': self.metadata
        }
    
    def rebuild_index(self):
        """
        重建索引（清空并重新加载所有文档）
        
        Examples:
            >>> kb = KnowledgeBase()
            >>> kb.rebuild_index()
        """
        print("🔄 重建索引...")
        
        # 清空 Chroma 集合
        try:
            self.collection.delete(where={})  # 删除所有
        except:
            pass
        
        # 清空元数据
        self.metadata.clear()
        
        # 重新加载
        self.load_documents_from_dir()
        
        # 保存缓存
        self.embeddings_manager.save_cache()
        
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
    
    def __repr__(self) -> str:
        """字符串表示"""
        stats = self.get_statistics()
        return (f"KnowledgeBase(files={stats['total_files']}, "
                f"chunks={stats['total_chunks']}, "
                f"size={stats['total_size_mb']}MB)")
