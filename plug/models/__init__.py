"""
PLUG Model Providers
=====================

Chat completion providers with tool-calling support.
"""

from plug.models.base import ChatProvider, Message, ToolCall, ChatResponse, ProviderChain
from plug.models.proxy import ProxyChatProvider
from plug.models.copilot import CopilotChatProvider
from plug.models.ollama import OllamaChatProvider

__all__ = [
    "ChatProvider",
    "Message",
    "ToolCall",
    "ChatResponse",
    "ProviderChain",
    "ProxyChatProvider",
    "CopilotChatProvider",
    "OllamaChatProvider",
]
