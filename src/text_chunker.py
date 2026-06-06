"""
语义感知文本分块 — 按标题/段落/语义边界分割，避免切断语境
"""
import re
from typing import List

from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_core.documents import Document

from config import CHUNK_SIZE, CHUNK_OVERLAP


class SemanticChunker:
    """
    语义感知分块器

    策略：
    1. 先按语义边界（标题、空行）拆分为段落
    2. 短段落直接保留，长段落用 RecursiveCharacterTextSplitter 细分
    3. 子块自动继承父标题，保持语境
    """

    # Markdown / reST 风格标题
    HEADING_PATTERN = re.compile(r'^(#{1,6}\s+.+)$', re.MULTILINE)

    def __init__(self, chunk_size: int = None, chunk_overlap: int = None):
        self.chunk_size = chunk_size or CHUNK_SIZE
        self.chunk_overlap = chunk_overlap or CHUNK_OVERLAP
        self._fallback = RecursiveCharacterTextSplitter(
            chunk_size=self.chunk_size,
            chunk_overlap=self.chunk_overlap,
            separators=["\n\n", "\n", "。", "，", " ", ""],
        )

    def split_text(self, text: str, source: str = "") -> List[str]:
        """主入口：语义分块"""
        return self._split_by_sections(text, source)

    def split_documents(self, docs: List[Document]) -> List[Document]:
        """批量处理 LangChain Document 列表"""
        result = []
        for doc in docs:
            source = doc.metadata.get("source", "")
            chunks = self.split_text(doc.page_content, source)
            for chunk in chunks:
                result.append(Document(page_content=chunk, metadata=dict(doc.metadata)))
        return result

    # Markdown / reST 风格标题
    HEADING_PATTERN = re.compile(r'^(#{1,6}\s+.+)$', re.MULTILINE)
    # 纯文本编号标题：如 "3. 机器学习的工作流程"、"2.1 监督学习"
    NUMBERED_SECTION = re.compile(r'^(\d+(?:\.\d+)*\s+.+)$', re.MULTILINE)
    # 子步骤标记：如 "步骤1：", "Step 1:", "第1步："
    SUBSTEP_PATTERN = re.compile(r'^(步骤\d+|Step\s*\d+|第\d+步)\s*[：:].*$', re.MULTILINE)

    # ---- 内部实现 ----

    def _split_by_sections(self, text: str, source: str) -> List[str]:
        sections = self._segment(text)
        chunks = []

        for heading, body in sections:
            if not body.strip():
                continue
            combined = f"{heading}\n{body}".strip() if heading else body

            if self._token_estimate(combined) <= self.chunk_size:
                chunks.append(combined)
            else:
                # 先尝试子步骤边界拆分
                sub_parts = self._split_by_substeps(body)
                for part in sub_parts:
                    full = f"{heading}\n{part}".strip() if heading else part
                    if self._token_estimate(full) <= self.chunk_size:
                        chunks.append(full)
                    else:
                        # 再按空行尝试
                        para_sections = self._segment_by_paragraphs(full)
                        for _, para_body in para_sections:
                            if self._token_estimate(para_body) <= self.chunk_size:
                                chunks.append(para_body)
                            else:
                                sub_chunks = self._fallback.split_text(para_body)
                                chunks.extend(sub_chunks)

        return chunks

    def _split_by_substeps(self, text: str) -> List[str]:
        """按子步骤边界（步骤1/步骤2...）拆分为组，合并相邻小步骤"""
        matches = list(self.SUBSTEP_PATTERN.finditer(text))
        if not matches:
            return [text]

        parts = []
        for i, m in enumerate(matches):
            start = m.start()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
            parts.append(text[start:end].strip())

        # 第一个子步骤之前的序言
        if matches[0].start() > 0:
            preamble = text[:matches[0].start()].strip()
            if preamble:
                parts.insert(0, preamble)

        # 合并小步骤，直到接近 chunk_size
        merged = []
        buf = ""
        for part in parts:
            candidate = buf + ("\n\n" + part if buf else part)
            if self._token_estimate(candidate) <= self.chunk_size:
                buf = candidate
            else:
                if buf:
                    merged.append(buf)
                buf = part
        if buf:
            merged.append(buf)

        return merged if len(merged) > 1 else [text]

    def _segment(self, text: str) -> List[tuple]:
        """
        按标题拆分为 (标题, 正文) 段列表
        优先 Markdown ##，其次纯文本编号标题 "3. xxx"
        """
        # 先尝试 Markdown 标题
        sections = self._split_by_pattern(text, self.HEADING_PATTERN)
        if sections:
            return sections

        # 再尝试编号标题（纯文本常见格式）
        sections = self._split_by_pattern(text, self.NUMBERED_SECTION)
        if sections:
            return sections

        # 都没有 → 按段落合并
        return self._segment_by_paragraphs(text)

    def _split_by_pattern(self, text: str, pattern: re.Pattern) -> List[tuple]:
        """按正则模式拆分文本为 (标题, 正文) 段"""
        matches = list(pattern.finditer(text))
        if not matches:
            return []

        sections = []
        for i, m in enumerate(matches):
            heading = m.group(1)
            start = m.end()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
            body = text[start:end].strip()
            if body.strip():
                sections.append((heading, body))

        # 第一个标题之前的文本作为序言
        if matches[0].start() > 0:
            preamble = text[:matches[0].start()].strip()
            if preamble:
                sections.insert(0, ("", preamble))

        return sections

    def _segment_by_paragraphs(self, text: str) -> List[tuple]:
        """按空行分段（无标题文档）"""
        paragraphs = re.split(r'\n\s*\n', text)
        # 合并短段落
        merged = []
        buf = ""
        for p in paragraphs:
            p = p.strip()
            if not p:
                continue
            if self._token_estimate(buf + "\n\n" + p) <= self.chunk_size:
                if buf:
                    buf += "\n\n" + p
                else:
                    buf = p
            else:
                if buf:
                    merged.append(("", buf))
                buf = p
        if buf:
            merged.append(("", buf))
        return merged if merged else [("", text)]

    @staticmethod
    def _token_estimate(text: str) -> int:
        """粗略 Token 估算（中文按字，英文按词）"""
        chinese_chars = len(re.findall(r'[\u4e00-\u9fff]', text))
        english_words = len(re.findall(r'[a-zA-Z]+', text))
        return chinese_chars + english_words
