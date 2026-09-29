import logging
import sys
from pythonjsonlogger import jsonlogger
from backend.utils.config import settings
from backend.utils.middleware import get_request_id

class CorrelationIdJsonFormatter(jsonlogger.JsonFormatter):
    def add_fields(self, log_record, record, message_dict):
        super().add_fields(log_record, record, message_dict)
        log_record["request_id"] = get_request_id()
        log_record["environment"] = settings.ENVIRONMENT
        log_record["service"] = settings.APP_NAME

class CorrelationIdStandardFormatter(logging.Formatter):
    def format(self, record):
        record.request_id = get_request_id()
        return super().format(record)

def setup_logging():
    """Sets up application-wide structured or console logging with request correlation IDs."""
    log_level = getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO)
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)

    # Clear existing handlers
    root_logger.handlers.clear()

    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(log_level)

    if settings.JSON_LOGS:
        formatter = CorrelationIdJsonFormatter(
            "%(asctime)s %(levelname)s %(name)s %(message)s"
        )
    else:
        formatter = CorrelationIdStandardFormatter(
            "[%(asctime)s] [%(levelname)s] [%(request_id)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        )

    handler.setFormatter(formatter)
    root_logger.addHandler(handler)

    # Silence chatty third-party loggers
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
