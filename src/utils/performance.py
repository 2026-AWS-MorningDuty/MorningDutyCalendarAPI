"""Small timers for handler and service performance logs."""

from contextlib import contextmanager
import time

from logger import logger


@contextmanager
def measure_perf(route, step):
    started_at = time.perf_counter()
    try:
        yield
    finally:
        logger.info(
            "[PERF] route=%s step=%s duration_ms=%.1f",
            route, step, (time.perf_counter() - started_at) * 1000,
        )
