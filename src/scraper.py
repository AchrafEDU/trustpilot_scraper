import urllib.parse
from typing import Optional

from loguru import logger
from playwright.async_api import BrowserContext
from playwright.async_api import TimeoutError as PWTimeout
from playwright_stealth import Stealth
from tenacity import retry, stop_after_attempt, wait_exponential

from .rate_limiter import global_rate_limiter


class BotBlockException(Exception):
    """Raised when Trustpilot blocks the request with a Cloudflare challenge."""

    pass


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
async def fetch_page(context: BrowserContext, url: str, wait_selector: str = "body") -> Optional[str]:
    """Fetches a page's HTML using an existing Playwright context, observing rate limits."""
    await global_rate_limiter.wait()

    page = None
    try:
        page = await context.new_page()
        await Stealth().apply_stealth_async(page)

        await page.goto(url, timeout=30_000, wait_until="domcontentloaded")

        for _ in range(5):
            title = await page.title()
            if "Verifying" in title or "Just a moment" in title or "Cloudflare" in title:
                await page.wait_for_timeout(2500)
            else:
                break

        title = await page.title()
        if "Verifying" in title or "Just a moment" in title or "Cloudflare" in title:
            raise BotBlockException(f"Bot block detected: title is '{title}'")

        try:
            await page.wait_for_selector(wait_selector, timeout=10_000)
        except PWTimeout:
            title = await page.title()
            if "Verifying" in title or "Just a moment" in title or "Cloudflare" in title:
                raise BotBlockException(f"Bot block detected after timeout: title is '{title}'")

        html = await page.content()
        await page.close()
        return html
    except Exception as e:
        logger.warning(f"Error fetching {url}: {e}")
        if page:
            try:
                await page.close()
            except Exception:
                pass
        raise


async def search_company(context: BrowserContext, company_name: str) -> Optional[str]:
    """Searches Trustpilot for a company name and returns the first result URL."""
    encoded_query = urllib.parse.quote_plus(company_name)
    url = f"https://fr.trustpilot.com/search?query={encoded_query}"

    html = await fetch_page(context, url, wait_selector="[name='business-unit-card']")
    if not html:
        return None

    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    first_result = soup.find("a", attrs={"name": "business-unit-card"})
    if first_result:
        href = first_result.get("href")
        if isinstance(href, str):
            return "https://fr.trustpilot.com" + href

    return None
