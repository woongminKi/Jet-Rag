from .admin import router as admin_router
from .answer import router as answer_router
from .auth import router as auth_router
from .documents import router as documents_router
from .me import router as me_router
from .search import router as search_router
from .stats import router as stats_router

__all__ = [
    "admin_router",
    "answer_router",
    "auth_router",
    "documents_router",
    "me_router",
    "search_router",
    "stats_router",
]
