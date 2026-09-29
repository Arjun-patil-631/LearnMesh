from typing import Any, Dict, Optional

class AppException(Exception):
    """Base application exception for LearnMesh."""
    def __init__(
        self,
        message: str,
        code: str = "INTERNAL_SERVER_ERROR",
        status_code: int = 500,
        details: Optional[Dict[str, Any]] = None
    ):
        super().__init__(message)
        self.message = message
        self.code = code
        self.status_code = status_code
        self.details = details or {}

class EntityNotFoundException(AppException):
    def __init__(self, entity_name: str, entity_id: str):
        super().__init__(
            message=f"{entity_name} with ID '{entity_id}' not found.",
            code="ENTITY_NOT_FOUND",
            status_code=404,
            details={"entity_name": entity_name, "entity_id": entity_id}
        )

class ValidationException(AppException):
    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(
            message=message,
            code="VALIDATION_ERROR",
            status_code=422,
            details=details or {}
        )

class ConflictException(AppException):
    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(
            message=message,
            code="RESOURCE_CONFLICT",
            status_code=409,
            details=details or {}
        )

class CircuitBreakerOpenException(AppException):
    def __init__(self, service_name: str, recovery_time_remaining: float):
        super().__init__(
            message=f"Service '{service_name}' is currently unavailable (circuit breaker OPEN). Please retry shortly.",
            code="CIRCUIT_BREAKER_OPEN",
            status_code=503,
            details={
                "service": service_name,
                "recovery_time_remaining_seconds": round(recovery_time_remaining, 1)
            }
        )

class DegradedServiceException(AppException):
    def __init__(self, message: str, service_name: str):
        super().__init__(
            message=message,
            code="SERVICE_DEGRADED",
            status_code=503,
            details={"service": service_name}
        )

class IdempotencyConflictException(AppException):
    def __init__(self, message: str = "A request with this Idempotency-Key is currently being processed."):
        super().__init__(
            message=message,
            code="IDEMPOTENCY_IN_PROGRESS",
            status_code=409
        )

# Enterprise aliases for standard error handling
ValidationError = ValidationException
ResourceNotFoundError = EntityNotFoundException

