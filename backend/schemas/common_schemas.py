import math
from typing import Generic, List, Literal, Optional, TypeVar, Any, Dict
from pydantic import BaseModel, Field

T = TypeVar("T")

class ErrorResponse(BaseModel):
    code: str
    message: str
    details: Dict[str, Any] = Field(default_factory=dict)
    request_id: str

class PaginatedResponse(BaseModel, Generic[T]):
    items: List[T]
    total: int
    page: int
    page_size: int
    total_pages: int

    @classmethod
    def create(cls, items: List[T], total: int, page: int, page_size: int):
        total_pages = math.ceil(total / page_size) if page_size > 0 else 1
        return cls(
            items=items,
            total=total,
            page=page,
            page_size=page_size,
            total_pages=total_pages
        )

class HealthCheckResponse(BaseModel):
    status: str
    environment: str
    timestamp: str
    version: str
    checks: Dict[str, Any]
