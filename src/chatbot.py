"""
对话机器人 - 支持基础对话、多轮对话、RAG 检索增强
"""
from typing import List, Dict, Optional
from src.llm_client import create_llm_client
from config import (
    SYSTEM_PROMPT,
    RAG_TOP_K,
    RAG_SYSTEM_PROMPT_TEMPLATE,
    RAG_CONTEXT_ITEM_TEMPLATE,
)


class Chatbot:
    """对话机器人 — 支持 RAG 检索增强生成"""

    def __init__(self, llm_provider: str = None, knowledge_base=None):
        """
        初始化对话机器人

        Args:
            llm_provider: LLM 提供商 (deepseek / local)
            knowledge_base: KnowledgeBase 实例（用于 RAG 检索）
        """
        self.client = create_llm_client(llm_provider)
        self.system_prompt = SYSTEM_PROMPT
        self.kb = knowledge_base
    def chat(self, user_message: str) -> str:
        """
        单轮对话
        
        Args:
            user_message: 用户输入
            
        Returns:
            机器人回复
        """
        messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": user_message}
        ]
        
        response = self.client.send_message(messages)
        return response
    
    def chat_with_history(self, user_message: str, history: List[Dict]) -> str:
        """
        带对话历史的多轮对话（第二阶段会用到）
        
        Args:
            user_message: 用户输入
            history: 对话历史 [{"role": "user"/"assistant", "content": "..."}, ...]
            
        Returns:
            机器人回复
        """
        messages = [
            {"role": "system", "content": self.system_prompt}
        ]
        
        # 添加历史消息
        messages.extend(history)
        
        # 添加当前用户消息
        messages.append({"role": "user", "content": user_message})
        
        response = self.client.send_message(messages)
        return response
    def chat_with_rag(self, user_message: str, history: List[Dict]) -> str:
        """
        带 RAG 检索增强的多轮对话

        流程：
        1. 在知识库中检索与用户问题相关的文档块
        2. 将检索到的文档片段格式化为上下文
        3. 构建增强版 system prompt（原 prompt + 检索上下文）
        4. 调用 LLM 生成回复

        Args:
            user_message: 用户输入
            history: 对话历史

        Returns:
            机器人回复（基于知识库增强）
        """
        # 如果没有知识库，退化为普通对话
        if self.kb is None:
            return self.chat_with_history(user_message, history)

        # 1. 检索相关文档
        results = self.kb.search(user_message, top_k=RAG_TOP_K)

        # 2. 格式化检索结果为上下文文本
        if results:
            context_parts = []
            for r in results:
                context_parts.append(
                    RAG_CONTEXT_ITEM_TEMPLATE.format(
                        source=r['source'],
                        chunk=r['chunk_index'],
                        score=r['score'],
                        content=r['content'],
                    )
                )
            context = "\n\n".join(context_parts)
        else:
            context = "（未找到相关文档）"

        # 3. 构建增强版 system prompt
        rag_system_prompt = RAG_SYSTEM_PROMPT_TEMPLATE.format(
            system_prompt=self.system_prompt,
            context=context,
        )

        # 4. 构建消息列表
        messages = [
            {"role": "system", "content": rag_system_prompt}
        ]
        messages.extend(history)
        messages.append({"role": "user", "content": user_message})

        response = self.client.send_message(messages)
        return response