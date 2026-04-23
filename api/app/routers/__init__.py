from .documents import router as documents_router
from .search import router as search_router
from .stats import router as stats_router

__all__ = ["documents_router", "search_router", "stats_router"]
