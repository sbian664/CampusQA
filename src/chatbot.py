"""
对话机器人 - 第一阶段基础实现
"""
from typing import List, Dict
from src.llm_client import create_llm_client
from config import SYSTEM_PROMPT


class Chatbot:
    """基础对话机器人"""
    
    def __init__(self, llm_provider: str = None):
        """
        初始化对话机器人
        
        Args:
            llm_provider: LLM 提供商 (deepseek / local)
        """
        self.client = create_llm_client(llm_provider)
        self.system_prompt = SYSTEM_PROMPT
    
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
