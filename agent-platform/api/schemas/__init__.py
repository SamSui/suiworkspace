"""网关请求/响应模型（Pydantic v2）。"""

from api.schemas.auth import (
    ApiKeyRotateResult,
    TokenRequest,
    TokenResponse,
    UserCreate,
    UserCreateResult,
    UserOut,
    user_to_out,
)
from api.schemas.chat import ChatRequest, ChatStartResponse, ChatStreamRequest, ResumeRequest
from api.schemas.common import ErrorBody, ErrorResponse, HealthResponse, StoreHealth
from api.schemas.kb import (
    KnowledgeBaseCreate,
    KnowledgeBaseOut,
    KnowledgeBaseUpdate,
    kb_to_out,
)

__all__ = [
    "ApiKeyRotateResult",
    "ChatRequest",
    "ChatStartResponse",
    "ChatStreamRequest",
    "ErrorBody",
    "ErrorResponse",
    "HealthResponse",
    "KnowledgeBaseCreate",
    "KnowledgeBaseOut",
    "KnowledgeBaseUpdate",
    "ResumeRequest",
    "StoreHealth",
    "TokenRequest",
    "TokenResponse",
    "UserCreate",
    "UserCreateResult",
    "UserOut",
    "kb_to_out",
    "user_to_out",
]
