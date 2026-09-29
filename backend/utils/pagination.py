from typing import Any, List, Optional, Tuple, Type
from sqlalchemy.orm import Query
from sqlalchemy import desc, asc

def apply_pagination_and_sorting(
    query: Query,
    model: Any,
    page: int = 1,
    page_size: int = 20,
    sort_by: Optional[str] = None,
    order: str = "desc"
) -> Tuple[List[Any], int]:
    """
    Applies filtering, sorting, and pagination to an SQLAlchemy query.
    Returns (items, total_count).
    """
    page = max(1, page)
    page_size = max(1, min(100, page_size))

    # Apply sorting if field exists on model
    if sort_by and hasattr(model, sort_by):
        sort_col = getattr(model, sort_by)
        query = query.order_by(desc(sort_col) if order.lower() == "desc" else asc(sort_col))
    elif hasattr(model, "created_at"):
        query = query.order_by(desc(getattr(model, "created_at")))

    total = query.count()
    offset = (page - 1) * page_size
    items = query.offset(offset).limit(page_size).all()

    return items, total
