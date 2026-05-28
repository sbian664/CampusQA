"""
LLM 客户端 - 支持多个提供商
"""
import requests
import json
from typing import List, Dict, Optional
from config import (
    LLM_PROVIDER, 
    DEEPSEEK_API_KEY, 
    DEEPSEEK_API_BASE,
    DEEPSEEK_MODEL,
    LOCAL_MODEL_BASE,
    LOCAL_MODEL_NAME,
    MAX_TOKENS,
    TEMPERATURE
)


class LLMClient:
    """LLM 客户端基类"""
    
    def __init__(self, provider: str = None):
        self.provider = provider or LLM_PROVIDER
        
    def send_message(self, messages: List[Dict], **kwargs) -> str:
        """发送消息到 LLM，返回响应文本"""
        raise NotImplementedError


class DeepSeekClient(LLMClient):
    """DeepSeek API 客户端"""
    
    def __init__(self):
        super().__init__("deepseek")
        self.api_key = DEEPSEEK_API_KEY
        self.base_url = DEEPSEEK_API_BASE
        self.model = DEEPSEEK_MODEL
        
        if not self.api_key:
            raise ValueError("DEEPSEEK_API_KEY 未设置，请在 .env 文件中配置")
    
    def send_message(self, messages: List[Dict], **kwargs) -> str:
        """调用 DeepSeek API"""
        url = f"{self.base_url}/chat/completions"
        
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        
        payload = {
            "model": self.model,
            "messages": messages,
            "max_tokens": kwargs.get("max_tokens", MAX_TOKENS),
            "temperature": kwargs.get("temperature", TEMPERATURE),
        }
        
        try:
            response = requests.post(url, json=payload, headers=headers, timeout=30)
            response.raise_for_status()
            
            data = response.json()
            return data["choices"][0]["message"]["content"]
        except requests.exceptions.RequestException as e:
            raise Exception(f"DeepSeek API 调用失败: {str(e)}")


class LocalModelClient(LLMClient):
    """本地模型客户端（预留接口）"""
    
    def __init__(self):
        super().__init__("local")
        self.base_url = LOCAL_MODEL_BASE
        self.model = LOCAL_MODEL_NAME
    
    def send_message(self, messages: List[Dict], **kwargs) -> str:
        """调用本地模型 API"""
        url = f"{self.base_url}/chat/completions"
        
        payload = {
            "model": self.model,
            "messages": messages,
            "max_tokens": kwargs.get("max_tokens", MAX_TOKENS),
            "temperature": kwargs.get("temperature", TEMPERATURE),
        }
        
        try:
            response = requests.post(url, json=payload, timeout=30)
            response.raise_for_status()
            
            data = response.json()
            return data["choices"][0]["message"]["content"]
        except requests.exceptions.RequestException as e:
            raise Exception(f"本地模型 API 调用失败: {str(e)}")


def create_llm_client(provider: str = None) -> LLMClient:
    """工厂函数：根据提供商创建 LLM 客户端"""
    provider = provider or LLM_PROVIDER
    
    if provider.lower() == "deepseek":
        return DeepSeekClient()
    elif provider.lower() == "local":
        return LocalModelClient()
    else:
        raise ValueError(f"未支持的 LLM 提供商: {provider}")
