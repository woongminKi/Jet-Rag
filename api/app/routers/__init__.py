from .admin import router as admin_router
from .answer import router as answer_router
from .documents import router as documents_router
from .search import router as search_router
from .stats import router as stats_router

__all__ = [
    "admin_router",
    "answer_router",
    "documents_router",
    "search_router",
    "stats_router",
]
