import asyncio
import uuid
import logging
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional

from backend.models.database import JobDB
from backend.repositories.db_session import get_db_context
from backend.utils.config import settings

logger = logging.getLogger("learnmesh.tasks")

class AsyncTaskQueue:
    """
    Asynchronous task queue with persistent database tracking,
    exponential retries, and dead-letter queue (DLQ) handling.
    """
    def __init__(self):
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=settings.TASK_QUEUE_MAX_SIZE)
        self._handlers: Dict[str, Callable] = {}
        self._workers: list[asyncio.Task] = []
        self._running: bool = False

    def register_handler(self, job_type: str, handler: Callable):
        """Register a callable worker function for a given job type."""
        self._handlers[job_type] = handler
        logger.info(f"Registered background task handler for '{job_type}'")

    async def start(self):
        """Start async worker tasks."""
        if self._running:
            return
        self._running = True
        for i in range(settings.TASK_QUEUE_WORKERS):
            task = asyncio.create_task(self._worker_loop(worker_id=i + 1))
            self._workers.append(task)
        logger.info(f"AsyncTaskQueue started with {settings.TASK_QUEUE_WORKERS} workers.")

    async def stop(self):
        """Gracefully drain and stop workers."""
        self._running = False
        for task in self._workers:
            task.cancel()
        await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers.clear()
        logger.info("AsyncTaskQueue workers stopped.")

    async def enqueue(
        self,
        job_type: str,
        payload: Dict[str, Any],
        max_attempts: int = 3
    ) -> str:
        """
        Persists job in JobDB and enqueues for async processing.
        Returns job_id.
        """
        job_id = f"job-{uuid.uuid4().hex[:12]}"
        now = datetime.now(timezone.utc)

        with get_db_context() as db:
            job = JobDB(
                id=job_id,
                job_type=job_type,
                status="pending",
                payload=payload,
                attempts=0,
                max_attempts=max_attempts,
                created_at=now,
                updated_at=now
            )
            db.add(job)

        await self._queue.put({"job_id": job_id, "job_type": job_type, "payload": payload})
        logger.info(f"Job {job_id} ({job_type}) enqueued.")
        return job_id

    async def _worker_loop(self, worker_id: int):
        while self._running:
            try:
                item = await self._queue.get()
                job_id = item["job_id"]
                job_type = item["job_type"]
                payload = item["payload"]

                await self._process_job(job_id, job_type, payload)
                self._queue.task_done()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Worker {worker_id} unexpected loop error: {e}")
                await asyncio.sleep(1.0)

    async def _process_job(self, job_id: str, job_type: str, payload: Dict[str, Any]):
        handler = self._handlers.get(job_type)
        if not handler:
            logger.error(f"No handler registered for job type '{job_type}'. Marking dead letter.")
            self._update_job_status(job_id, status="dead_letter", error=f"Missing handler: {job_type}")
            return

        with get_db_context() as db:
            job = db.query(JobDB).filter(JobDB.id == job_id).first()
            if not job:
                return
            job.status = "running"
            job.attempts += 1
            attempts = job.attempts
            max_attempts = job.max_attempts
            job.updated_at = datetime.now(timezone.utc)

        try:
            # Execute handler (async or sync)
            if asyncio.iscoroutinefunction(handler):
                result = await handler(payload)
            else:
                loop = asyncio.get_running_loop()
                result = await loop.run_in_executor(None, handler, payload)

            self._update_job_status(job_id, status="completed", result=result)
            logger.info(f"Job {job_id} ({job_type}) completed successfully.")
        except Exception as e:
            logger.warning(f"Job {job_id} failed on attempt {attempts}/{max_attempts}: {e}")
            if attempts < max_attempts:
                # Retry with delay
                self._update_job_status(job_id, status="pending", error=str(e))
                await asyncio.sleep(2.0 * attempts)
                await self._queue.put({"job_id": job_id, "job_type": job_type, "payload": payload})
            else:
                logger.error(f"Job {job_id} exhausted all {max_attempts} attempts. Moving to dead letter queue.")
                self._update_job_status(job_id, status="dead_letter", error=f"Exhausted retries: {str(e)}")

    def _update_job_status(
        self,
        job_id: str,
        status: str,
        result: Optional[Any] = None,
        error: Optional[str] = None
    ):
        with get_db_context() as db:
            job = db.query(JobDB).filter(JobDB.id == job_id).first()
            if job:
                job.status = status
                job.updated_at = datetime.now(timezone.utc)
                if result is not None:
                    job.result = result if isinstance(result, dict) else {"result": str(result)}
                if error is not None:
                    job.error = error
                if status in ("completed", "dead_letter", "failed"):
                    job.completed_at = datetime.now(timezone.utc)

    def get_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        with get_db_context() as db:
            job = db.query(JobDB).filter(JobDB.id == job_id).first()
            if not job:
                return None
            return {
                "id": job.id,
                "job_type": job.job_type,
                "status": job.status,
                "payload": job.payload,
                "result": job.result,
                "error": job.error,
                "attempts": job.attempts,
                "max_attempts": job.max_attempts,
                "created_at": job.created_at.isoformat() if job.created_at else None,
                "updated_at": job.updated_at.isoformat() if job.updated_at else None,
                "completed_at": job.completed_at.isoformat() if job.completed_at else None
            }

task_queue = AsyncTaskQueue()
