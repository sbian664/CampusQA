"""
文档加载器 - 支持多种格式（MD, TXT, PDF, HTML）
"""
import os
from pathlib import Path
from typing import List, Dict, Optional
from datetime import datetime

from langchain_community.document_loaders import TextLoader, PyPDFLoader
from langchain_core.documents import Document
from bs4 import BeautifulSoup

from config import SUPPORTED_FORMATS, DOCUMENTS_DIR


class DocumentLoader:
    """多格式文档加载器"""
    
    def __init__(self, base_dir: str = None):
        """
        初始化文档加载器
        
        Args:
            base_dir (str): 文档基目录，默认使用 config.DOCUMENTS_DIR
        """
        self.base_dir = base_dir or DOCUMENTS_DIR
        self.supported_formats = set(SUPPORTED_FORMATS)
        
        # 确保目录存在
        os.makedirs(self.base_dir, exist_ok=True)
    
    def load_file(self, file_path: str) -> List[Document]:
        """
        加载单个文件
        
        Args:
            file_path (str): 文件路径（绝对路径或相对于 base_dir）
        
        Returns:
            List[Document]: 加载的文档列表
        
        Raises:
            ValueError: 不支持的文件格式
            FileNotFoundError: 文件不存在
        
        Examples:
            >>> loader = DocumentLoader()
            >>> docs = loader.load_file("readme.md")
            >>> len(docs)
            1
        """
        # 处理相对路径
        if not os.path.isabs(file_path):
            file_path = os.path.join(self.base_dir, file_path)
        
        file_path = os.path.abspath(file_path)
        
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"文件不存在: {file_path}")
        
        # 获取文件扩展名
        ext = Path(file_path).suffix.lower()
        
        if ext not in self.supported_formats:
            raise ValueError(f"不支持的文件格式: {ext}，支持的格式: {self.supported_formats}")
        
        # 根据格式调用相应的加载器
        if ext == '.md':
            return self._load_markdown(file_path)
        elif ext == '.txt':
            return self._load_text(file_path)
        elif ext == '.pdf':
            return self._load_pdf(file_path)
        elif ext == '.html':
            return self._load_html(file_path)
    
    def _load_markdown(self, file_path: str) -> List[Document]:
        """加载 Markdown 文件"""
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            # 获取文件元数据
            metadata = self._get_file_metadata(file_path)
            
            doc = Document(
                page_content=content,
                metadata={
                    **metadata,
                    'format': 'markdown'
                }
            )
            return [doc]
        except Exception as e:
            raise Exception(f"加载 Markdown 文件失败 {file_path}: {str(e)}")
    
    def _load_text(self, file_path: str) -> List[Document]:
        """加载纯文本文件"""
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            metadata = self._get_file_metadata(file_path)
            
            doc = Document(
                page_content=content,
                metadata={
                    **metadata,
                    'format': 'text'
                }
            )
            return [doc]
        except Exception as e:
            raise Exception(f"加载文本文件失败 {file_path}: {str(e)}")
    
    def _load_pdf(self, file_path: str) -> List[Document]:
        """加载 PDF 文件"""
        try:
            loader = PyPDFLoader(file_path)
            docs = loader.load()
            
            # 添加元数据
            metadata = self._get_file_metadata(file_path)
            for doc in docs:
                doc.metadata.update(metadata)
                doc.metadata['format'] = 'pdf'
            
            return docs
        except Exception as e:
            raise Exception(f"加载 PDF 文件失败 {file_path}: {str(e)}")
    
    def _load_html(self, file_path: str) -> List[Document]:
        """加载 HTML 文件"""
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                html_content = f.read()
            
            # 使用 BeautifulSoup 提取主要内容
            soup = BeautifulSoup(html_content, 'html5lib')
            
            # 提取文本（移除脚本和样式）
            for script in soup(['script', 'style']):
                script.decompose()
            
            # 获取主要文本
            text_content = soup.get_text(separator='\n', strip=True)
            
            # 获取标题
            title = soup.title.string if soup.title else "Untitled"
            
            metadata = self._get_file_metadata(file_path)
            metadata['title'] = title
            
            doc = Document(
                page_content=text_content,
                metadata={
                    **metadata,
                    'format': 'html'
                }
            )
            return [doc]
        except Exception as e:
            raise Exception(f"加载 HTML 文件失败 {file_path}: {str(e)}")
    
    def _get_file_metadata(self, file_path: str) -> Dict:
        """提取文件元数据"""
        stat = os.stat(file_path)
        return {
            'source': file_path,
            'filename': os.path.basename(file_path),
            'size': stat.st_size,
            'mtime': stat.st_mtime,
            'mtime_str': datetime.fromtimestamp(stat.st_mtime).isoformat()
        }
    
    def load_directory(self, 
                      patterns: List[str] = None,
                      recursive: bool = True) -> List[Document]:
        """
        批量加载目录中的文档
        
        Args:
            patterns (List[str]): 文件模式，默认为所有支持的格式
            recursive (bool): 是否递归子目录
        
        Returns:
            List[Document]: 加载的所有文档
        
        Examples:
            >>> loader = DocumentLoader()
            >>> docs = loader.load_directory()
            >>> print(f"加载了 {len(docs)} 个文档")
        """
        if patterns is None:
            patterns = [f"*{fmt}" for fmt in self.supported_formats]
        
        documents = []
        
        for pattern in patterns:
            if recursive:
                file_paths = Path(self.base_dir).rglob(pattern)
            else:
                file_paths = Path(self.base_dir).glob(pattern)
            
            for file_path in file_paths:
                if file_path.is_file():
                    try:
                        docs = self.load_file(str(file_path))
                        documents.extend(docs)
                    except Exception as e:
                        print(f"⚠️  加载文件失败 {file_path}: {str(e)}")
        
        return documents
    
    def get_file_list(self, recursive: bool = True) -> List[Dict]:
        """
        获取目录中所有支持格式的文件列表
        
        Args:
            recursive (bool): 是否递归子目录
        
        Returns:
            List[Dict]: 文件信息列表
                       [{"path": "...", "size": 123, "mtime": ...}, ...]
        
        Examples:
            >>> loader = DocumentLoader()
            >>> files = loader.get_file_list()
            >>> for f in files:
            ...     print(f"{f['filename']}: {f['size']} bytes")
        """
        files = []
        
        for pattern in [f"*{fmt}" for fmt in self.supported_formats]:
            if recursive:
                file_paths = Path(self.base_dir).rglob(pattern)
            else:
                file_paths = Path(self.base_dir).glob(pattern)
            
            for file_path in file_paths:
                if file_path.is_file():
                    stat = os.stat(file_path)
                    files.append({
                        'path': str(file_path),
                        'filename': file_path.name,
                        'size': stat.st_size,
                        'mtime': stat.st_mtime,
                        'format': file_path.suffix.lower()
                    })
        
        return files
    
    def __repr__(self) -> str:
        """字符串表示"""
        return f"DocumentLoader(base_dir={self.base_dir})"
