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

    @staticmethod
    def _extract_tool_name(tool_call: Dict) -> str:
        return (
            tool_call.get("tool_name")
            or tool_call.get("function", {}).get("name")
            or ""
        ).strip()

    @staticmethod
    def _extract_tool_arguments(tool_call: Dict) -> Dict:
        arguments = tool_call.get("arguments")
        if arguments is None:
            arguments = tool_call.get("function", {}).get("arguments")
        if isinstance(arguments, str):
            try:
                parsed = json.loads(arguments)
            except json.JSONDecodeError:
                return {}
            return parsed if isinstance(parsed, dict) else {}
        return arguments if isinstance(arguments, dict) else {}

    @classmethod
    def _summarize_tool_calls(cls, tool_calls: List[Dict]) -> str:
        search_queries = []
        tool_names = []

        for tool_call in tool_calls:
            name = cls._extract_tool_name(tool_call)
            args = cls._extract_tool_arguments(tool_call)
            query = str(args.get("query", "")).strip()

            if name == "search_knowledge_base" and query:
                if query not in search_queries:
                    search_queries.append(query)
            elif name and name not in tool_names:
                tool_names.append(name)

        if search_queries:
            return f"检索关键词: {', '.join(search_queries)}"
        if tool_names:
            return f"调用了工具: {', '.join(tool_names)}"
        return ""
    
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
        self.tool_call_log: List[Dict] = []  # Agent 模式工具调用日志
        self.total_prompt_tokens: int = 0    # 累计 prompt tokens
        self.total_completion_tokens: int = 0# 累计 completion tokens

        # 确保缓存目录存在
        os.makedirs(self.cache_dir, exist_ok=True)
    
    def add_message(self, role: str, content: str = None, 
                    tool_calls: List[Dict] = None,
                    tool_call_id: str = None,
                    name: str = None) -> None:
        """
        添加消息到历史（扩展支持 tool calling 格式）
        
        Args:
            role: 消息角色 ("user" / "assistant" / "tool")
            content: 消息文本内容（tool 角色时可为 None）
            tool_calls: assistant 的 tool_calls 列表（仅 role="assistant" 时）
            tool_call_id: tool 角色关联的 tool_call id（仅 role="tool" 时）
            name: 工具名称（仅 role="tool" 时可选）
        """
        valid_roles = ["user", "assistant", "tool"]
        if role not in valid_roles:
            raise ValueError(f"角色必须是 {valid_roles} 之一，收到: {role}")
        
        message = {
            "role": role,
            "timestamp": datetime.now().isoformat()
        }

        if content is not None:
            message["content"] = content

        if tool_calls is not None:
            message["tool_calls"] = tool_calls

        if tool_call_id is not None:
            message["tool_call_id"] = tool_call_id

        if name is not None:
            message["name"] = name
        
        self.messages.append(message)
        self.metadata["message_count"] += 1
        self.metadata["updated_at"] = datetime.now().isoformat()
        
        # 防止历史过长导致token溢出
        if len(self.messages) > self.max_history:
            self.messages = self.messages[-self.max_history:]
    
    def get_history(self, include_timestamp: bool = False,
                    strip_tool_details: bool = False) -> List[Dict]:
        """
        获取对话历史（格式兼容 OpenAI API）
        
        Args:
            include_timestamp: 是否包含时间戳
            strip_tool_details: True 时过滤掉 tool_calls 和 tool 消息的内部细节，
                               仅保留 user/assistant 的纯文本视图
        """
        if include_timestamp:
            return self.messages.copy()

        if strip_tool_details:
            # 只返回 user/assistant 消息，去除 tool 相关细节
            result = []
            for msg in self.messages:
                if msg["role"] == "tool":
                    continue  # 跳过工具结果消息
                entry = {"role": msg["role"]}
                # 保留 content，若有 tool_calls 则只保留函数名摘要
                if "content" in msg:
                    entry["content"] = msg["content"]
                if "tool_calls" in msg:
                    # 简化为摘要
                    summary = self._summarize_tool_calls(msg["tool_calls"])
                    if summary:
                        entry["content"] = (entry.get("content", "") or "") + f" [{summary}]"
                result.append(entry)
            return result

        # 去除时间戳，返回完整 OpenAI 兼容格式
        result = []
        for msg in self.messages:
            entry = {"role": msg["role"]}
            if "content" in msg:
                entry["content"] = msg["content"]
            if "tool_calls" in msg:
                entry["tool_calls"] = msg["tool_calls"]
            if "tool_call_id" in msg:
                entry["tool_call_id"] = msg["tool_call_id"]
            if "name" in msg:
                entry["name"] = msg["name"]
            result.append(entry)
        return result
    
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
            "metadata": self.metadata,
            "tool_call_log": self.tool_call_log,
            "total_prompt_tokens": self.total_prompt_tokens,
            "total_completion_tokens": self.total_completion_tokens,
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
            self.tool_call_log = data.get("tool_call_log", [])
            self.total_prompt_tokens = data.get("total_prompt_tokens", 0)
            self.total_completion_tokens = data.get("total_completion_tokens", 0)
            
            return True
        except Exception as e:
            raise Exception(f"加载会话失败: {str(e)}")
    
    @staticmethod
    def _is_session_file(filepath: str) -> bool:
        """判断 JSON 文件是否是会话文件，而不是知识库缓存元数据。"""
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                data = json.load(f)
            return (
                isinstance(data, dict)
                and isinstance(data.get("session_id"), str)
                and isinstance(data.get("messages"), list)
                and isinstance(data.get("metadata"), dict)
            )
        except Exception:
            return False

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
            return [
                f for f in files
                if f.endswith('.json')
                and Session._is_session_file(os.path.join(CACHE_DIR, f))
            ]
        except:
            return []
    
    def __repr__(self) -> str:
        """字符串表示"""
        return f"Session(id={self.session_id}, messages={len(self.messages)})"

    # ---- Agent 模式扩展方法 ----

    def get_tool_call_log(self) -> List[Dict]:
        """获取当前会话的工具调用日志"""
        return self.tool_call_log.copy()

    def append_tool_call_log(self, entries: List[Dict]):
        """批量追加工具调用日志条目"""
        self.tool_call_log.extend(entries)

    def accumulate_usage(self, usage: Dict):
        """累积 token 用量"""
        if usage:
            self.total_prompt_tokens += usage.get("prompt_tokens", 0)
            self.total_completion_tokens += usage.get("completion_tokens", 0)

    def get_cost_summary(self) -> str:
        """获取 token 消耗摘要"""
        total = self.total_prompt_tokens + self.total_completion_tokens
        return (
            f"📊 Token 消耗:\n"
            f"  Prompt:     {self.total_prompt_tokens:,}\n"
            f"  Completion: {self.total_completion_tokens:,}\n"
            f"  合计:       {total:,}\n"
            f"  工具调用:   {len(self.tool_call_log)} 次"
        )
