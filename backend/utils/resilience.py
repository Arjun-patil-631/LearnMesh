import time
import random
import logging
import functools
from enum import Enum
from typing import Any, Callable, Dict, Optional, Tuple, Type

from backend.utils.config import settings
from backend.utils.exceptions import CircuitBreakerOpenException

logger = logging.getLogger("learnmesh.resilience")

class CircuitState(str, Enum):
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"

class CircuitBreaker:
    """
    Production-grade Circuit Breaker pattern.
    Protects downstream dependencies (Hindsight, Groq) from cascading failures.
    """
    def __init__(
        self,
        name: str,
        failure_threshold: int = 3,
        recovery_timeout_seconds: float = 30.0,
        expected_exceptions: Tuple[Type[Exception], ...] = (Exception,)
    ):
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout_seconds = recovery_timeout_seconds
        self.expected_exceptions = expected_exceptions

        self.state: CircuitState = CircuitState.CLOSED
        self.failure_count: int = 0
        self.last_failure_time: Optional[float] = None
        self.total_trips: int = 0
        self.successful_calls: int = 0
        self.failed_calls: int = 0

    def can_execute(self) -> bool:
        if self.state == CircuitState.CLOSED:
            return True

        if self.state == CircuitState.OPEN:
            now = time.time()
            if self.last_failure_time and (now - self.last_failure_time >= self.recovery_timeout_seconds):
                logger.info(f"CircuitBreaker '{self.name}': Recovery timeout expired. Transitioning OPEN -> HALF_OPEN.")
                self.state = CircuitState.HALF_OPEN
                return True
            return False

        # HALF_OPEN: allow a single probationary trial
        return True

    def reset(self):
        """Manually reset circuit breaker state to CLOSED."""
        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.last_failure_time = None

    def record_success(self):
        self.successful_calls += 1
        if self.state in (CircuitState.HALF_OPEN, CircuitState.OPEN):
            logger.info(f"CircuitBreaker '{self.name}': Trial probe succeeded. Resetting {self.state} -> CLOSED.")
        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.last_failure_time = None

    def record_failure(self, error: Exception):
        self.failed_calls += 1
        self.failure_count += 1
        self.last_failure_time = time.time()
        logger.warning(
            f"CircuitBreaker '{self.name}': Failure recorded ({type(error).__name__}: {error}). "
            f"Count: {self.failure_count}/{self.failure_threshold}"
        )

        if self.state == CircuitState.HALF_OPEN or self.failure_count >= self.failure_threshold:
            if self.state != CircuitState.OPEN:
                self.total_trips += 1
                logger.error(
                    f"CircuitBreaker '{self.name}': Threshold exceeded ({self.failure_count}). "
                    f"Tripping circuit to OPEN for {self.recovery_timeout_seconds}s."
                )
            self.state = CircuitState.OPEN

    def time_until_recovery(self) -> float:
        if self.state != CircuitState.OPEN or not self.last_failure_time:
            return 0.0
        remaining = self.recovery_timeout_seconds - (time.time() - self.last_failure_time)
        return max(0.0, remaining)

    def execute(self, func: Callable, *args, **kwargs) -> Any:
        if not settings.FEATURE_CIRCUIT_BREAKER:
            return func(*args, **kwargs)

        if not self.can_execute():
            raise CircuitBreakerOpenException(
                service_name=self.name,
                recovery_time_remaining=self.time_until_recovery()
            )

        try:
            result = func(*args, **kwargs)
            self.record_success()
            return result
        except self.expected_exceptions as e:
            self.record_failure(e)
            raise

    def get_status(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "state": self.state.value,
            "failure_count": self.failure_count,
            "failure_threshold": self.failure_threshold,
            "recovery_timeout_seconds": self.recovery_timeout_seconds,
            "time_until_recovery_seconds": round(self.time_until_recovery(), 1),
            "successful_calls": self.successful_calls,
            "failed_calls": self.failed_calls,
            "total_trips": self.total_trips
        }

def retry_with_exponential_backoff(
    max_attempts: int = 3,
    base_delay: float = 0.5,
    max_delay: float = 5.0,
    jitter: bool = True,
    retryable_exceptions: Tuple[Type[Exception], ...] = (Exception,)
):
    """
    Decorator executing retry with full jitter backoff:
    t_sleep = min(max_delay, base_delay * (2 ** attempt)) + random_jitter
    """
    def decorator(func: Callable):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            last_exception = None
            for attempt in range(max_attempts):
                try:
                    return func(*args, **kwargs)
                except retryable_exceptions as e:
                    last_exception = e
                    if attempt == max_attempts - 1:
                        logger.error(f"Function {func.__name__} exhausted {max_attempts} retry attempts. Error: {e}")
                        raise
                    
                    delay = min(max_delay, base_delay * (2 ** attempt))
                    if jitter:
                        delay = delay * random.uniform(0.75, 1.25)
                    
                    logger.warning(
                        f"Attempt {attempt + 1}/{max_attempts} for {func.__name__} failed ({e}). "
                        f"Retrying in {delay:.2f}s..."
                    )
                    time.sleep(delay)
            raise last_exception  # fallback
        return wrapper
    return decorator

# Global circuit breaker singletons for core external dependencies
hindsight_circuit_breaker = CircuitBreaker(
    name="Hindsight",
    failure_threshold=settings.CB_FAILURE_THRESHOLD,
    recovery_timeout_seconds=settings.CB_RECOVERY_TIMEOUT_SECONDS
)

groq_circuit_breaker = CircuitBreaker(
    name="GroqLLM",
    failure_threshold=settings.CB_FAILURE_THRESHOLD,
    recovery_timeout_seconds=settings.CB_RECOVERY_TIMEOUT_SECONDS
)
