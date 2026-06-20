import asyncio
import time

from loguru import logger


class AsyncRateLimiter:
    """Simple rate limiter ensuring a minimum delay between requests asynchronously."""

    def __init__(self, delay_seconds: float) -> None:
        """Initialize the rate limiter.

        Args:
            delay_seconds: Minimum seconds to wait between requests.
        """
        self.delay_seconds = delay_seconds
        self.last_request_time = 0.0
        self._lock = asyncio.Lock()

    async def wait(self) -> None:
        """Asynchronously blocks until enough time has passed since the last request."""
        async with self._lock:
            current_time = time.monotonic()
            elapsed = current_time - self.last_request_time
            if elapsed < self.delay_seconds:
                sleep_time = self.delay_seconds - elapsed
                logger.debug(f"Rate limiting: sleeping for {sleep_time:.2f}s")
                await asyncio.sleep(sleep_time)

            self.last_request_time = time.monotonic()


# Global singleton for the scraper (e.g., 1 request per 2.5 seconds)
global_rate_limiter = AsyncRateLimiter(2.5)
