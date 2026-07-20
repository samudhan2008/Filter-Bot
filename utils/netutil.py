"""
Small dependency-free helpers used by anything that calls an external
service (TMDB, the SC Files backend, the shortlink API):

- `retry_async`: exponential backoff retry decorator for async functions.
- `CircuitBreaker`: after too many consecutive failures, stop calling the
  service for a cooldown window and return immediately, instead of letting
  every incoming search hang while a dead backend times out repeatedly.
"""

import asyncio
import functools
import logging
import time

logger = logging.getLogger(__name__)


def retry_async(retries=3, base_delay=0.5, max_delay=8.0, exceptions=(Exception,)):
    def decorator(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            delay = base_delay
            last_exc = None
            for attempt in range(1, retries + 1):
                try:
                    return await func(*args, **kwargs)
                except exceptions as e:
                    last_exc = e
                    if attempt == retries:
                        break
                    logger.warning(f"{func.__name__} failed (attempt {attempt}/{retries}): {e}. Retrying in {delay:.1f}s")
                    await asyncio.sleep(delay)
                    delay = min(delay * 2, max_delay)
            logger.error(f"{func.__name__} failed after {retries} attempts: {last_exc}")
            raise last_exc
        return wrapper
    return decorator


class CircuitBreaker:
    """
    Per-service breaker. After `fail_threshold` consecutive failures, the
    breaker "opens" and `allow()` returns False for `cooldown` seconds,
    so callers can short-circuit (return cached/empty data) instead of
    hammering a dead service on every request.
    """

    def __init__(self, fail_threshold=5, cooldown=60):
        self.fail_threshold = fail_threshold
        self.cooldown = cooldown
        self._fails = 0
        self._opened_at = None

    def allow(self) -> bool:
        if self._opened_at is None:
            return True
        if time.time() - self._opened_at >= self.cooldown:
            # half-open: let one request through to test the waters
            self._opened_at = None
            self._fails = 0
            return True
        return False

    def record_success(self):
        self._fails = 0
        self._opened_at = None

    def record_failure(self):
        self._fails += 1
        if self._fails >= self.fail_threshold and self._opened_at is None:
            self._opened_at = time.time()
            logger.warning(f"Circuit breaker opened after {self._fails} consecutive failures; "
                            f"cooling down for {self.cooldown}s")

    @property
    def is_open(self) -> bool:
        return not self.allow()
