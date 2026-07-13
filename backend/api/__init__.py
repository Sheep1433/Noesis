from .user_api import user_router as user_router
from .chat_api import chat_router as chat_router
from .knowledge_base_api import knowledge_base_router as knowledge_base_router
from .skill_api import skill_router as skill_router
from .chat_attachment_api import chat_attachment_router as chat_attachment_router
from .model_api import model_router as model_router
from .auth_api import auth_router as auth_router

__all__ = [
    "user_router",
    "chat_router",
    "knowledge_base_router",
    "skill_router",
    "chat_attachment_router",
    "model_router",
    "auth_router",
]
