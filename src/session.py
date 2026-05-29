"""
会话管理 - 对话历史和上下文管理
"""
import json
import os
from datetime import datetime
from typing import List, Dict, Optional
from config import CACHE_DIR


class Session:
    """对话会话类 - 管理对话历史和上下文"""
    
    def __init__(self, session_id: str = None, max_history: int = 20):
        """
        初始化会话
        
        Args:
            session_id (str): 会话ID，如果为None则生成新ID
            max_history (int): 保留的最大历史消息数（防止token溢出）
        """
        if session_id is None:
            session_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        self.session_id = session_id
        self.max_history = max_history
        self.messages: List[Dict] = []  # 对话历史
        self.metadata = {
            "created_at": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat(),
            "message_count": 0
        }
        self.cache_dir = CACHE_DIR
        
        # 确保缓存目录存在
        os.makedirs(self.cache_dir, exist_ok=True)
    
    def add_message(self, role: str, content: str) -> None:
        """
        添加消息到历史
        
        Args:
            role (str): 消息角色 ("user" 或 "assistant")
            content (str): 消息内容
        
        Examples:
            >>> session = Session()
            >>> session.add_message("user", "你好")
            >>> session.add_message("assistant", "你好，很高兴为你服务")
        """
        if role not in ["user", "assistant"]:
            raise ValueError(f"角色必须是 'user' 或 'assistant'，收到: {role}")
        
        message = {
            "role": role,
            "content": content,
            "timestamp": datetime.now().isoformat()
        }
        
        self.messages.append(message)
        self.metadata["message_count"] += 1
        self.metadata["updated_at"] = datetime.now().isoformat()
        
        # 防止历史过长导致token溢出
        if len(self.messages) > self.max_history:
            self.messages = self.messages[-self.max_history:]
    
    def get_history(self, include_timestamp: bool = False) -> List[Dict]:
        """
        获取对话历史（格式兼容OpenAI API）
        
        Args:
            include_timestamp (bool): 是否包含时间戳
        
        Returns:
            List[Dict]: 消息列表，格式 [{"role": "...", "content": "..."}, ...]
        
        Examples:
            >>> session = Session()
            >>> session.add_message("user", "你好")
            >>> session.add_message("assistant", "你好")
            >>> history = session.get_history()
            >>> len(history)
            2
        """
        if include_timestamp:
            return self.messages.copy()
        
        # 去除时间戳，返回OpenAI兼容格式
        return [
            {"role": msg["role"], "content": msg["content"]}
            for msg in self.messages
        ]
    
    def get_last_exchange(self) -> Optional[Dict]:
        """
        获取最后一次对话交换（用户消息 + AI回复）
        
        Returns:
            Dict: 包含 user 和 assistant 的最后一次交换，如果无交换则返回None
        
        Examples:
            >>> session = Session()
            >>> session.add_message("user", "问题")
            >>> session.add_message("assistant", "回答")
            >>> exchange = session.get_last_exchange()
            >>> exchange["user"]
            '问题'
        """
        if len(self.messages) < 2:
            return None
        
        # 查找最后一个用户消息和最后一个助手消息
        user_msg = None
        assistant_msg = None
        
        for msg in reversed(self.messages):
            if msg["role"] == "assistant" and assistant_msg is None:
                assistant_msg = msg
            elif msg["role"] == "user" and user_msg is None:
                user_msg = msg
            
            if user_msg and assistant_msg:
                break
        
        if user_msg and assistant_msg:
            return {
                "user": user_msg["content"],
                "assistant": assistant_msg["content"]
            }
        
        return None
    
    def clear(self) -> None:
        """
        清空对话历史
        
        Examples:
            >>> session = Session()
            >>> session.add_message("user", "你好")
            >>> session.clear()
            >>> len(session.get_history())
            0
        """
        self.messages = []
        self.metadata["message_count"] = 0
        self.metadata["updated_at"] = datetime.now().isoformat()
    
    def get_context_summary(self, max_chars: int = 500) -> str:
        """
        获取对话上下文摘要（用于调试或显示）
        
        Args:
            max_chars (int): 摘要最大字符数
        
        Returns:
            str: 格式化的上下文摘要
        
        Examples:
            >>> session = Session()
            >>> session.add_message("user", "你好")
            >>> session.add_message("assistant", "你好")
            >>> print(session.get_context_summary())
        """
        if not self.messages:
            return "（无对话历史）"
        
        summary = f"会话ID: {self.session_id}\n"
        summary += f"消息数: {len(self.messages)}\n"
        summary += f"创建时间: {self.metadata['created_at']}\n"
        summary += f"最后更新: {self.metadata['updated_at']}\n"
        summary += "-" * 40 + "\n"
        
        current_chars = len(summary)
        
        for msg in self.messages:
            role = "👤 用户" if msg["role"] == "user" else "🤖 助手"
            msg_str = f"{role}: {msg['content']}\n"
            
            if current_chars + len(msg_str) > max_chars:
                summary += "...\n"
                break
            
            summary += msg_str
            current_chars += len(msg_str)
        
        return summary
    
    def save(self, filename: str = None) -> str:
        """
        保存会话到文件
        
        Args:
            filename (str): 文件名，如果为None则使用会话ID
        
        Returns:
            str: 保存的文件路径
        
        Examples:
            >>> session = Session()
            >>> session.add_message("user", "你好")
            >>> path = session.save()
            >>> print(path)
        """
        if filename is None:
            filename = f"{self.session_id}.json"
        
        filepath = os.path.join(self.cache_dir, filename)
        
        data = {
            "session_id": self.session_id,
            "messages": self.messages,
            "metadata": self.metadata
        }
        
        try:
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            return filepath
        except Exception as e:
            raise Exception(f"保存会话失败: {str(e)}")
    
    def load(self, filename: str = None) -> bool:
        """
        从文件加载会话
        
        Args:
            filename (str): 文件名，如果为None则使用会话ID
        
        Returns:
            bool: 是否加载成功
        
        Examples:
            >>> session = Session("old_session_id")
            >>> session.load()
            True
        """
        if filename is None:
            filename = f"{self.session_id}.json"
        
        filepath = os.path.join(self.cache_dir, filename)
        
        if not os.path.exists(filepath):
            return False
        
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            self.session_id = data.get("session_id", self.session_id)
            self.messages = data.get("messages", [])
            self.metadata = data.get("metadata", self.metadata)
            
            return True
        except Exception as e:
            raise Exception(f"加载会话失败: {str(e)}")
    
    def list_saved_sessions() -> List[str]:
        """
        列出所有保存的会话文件
        
        Returns:
            List[str]: 保存的会话文件列表
        
        Examples:
            >>> sessions = Session.list_saved_sessions()
            >>> print(sessions)
        """
        try:
            files = os.listdir(CACHE_DIR)
            return [f for f in files if f.endswith('.json')]
        except:
            return []
    
    def __repr__(self) -> str:
        """字符串表示"""
        return f"Session(id={self.session_id}, messages={len(self.messages)})"
