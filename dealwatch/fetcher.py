"""Fetch prices from Indian e-commerce product pages."""

import logging
import random
import re
import time

import requests

logger = logging.getLogger("dealwatch.fetcher")

MAX_RETRIES = 3
BACKOFF_SECONDS = 1.0

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64; rv:127.0) Gecko/20100101 Firefox/127.0",
]


def _headers() -> dict[str, str]:
    """Fresh headers with a randomly picked user agent."""
    return {"User-Agent": random.choice(USER_AGENTS)}


def _get_with_retry(url: str, timeout: int) -> requests.Response:
    """GET a page, retrying transient failures with exponential backoff."""
    last_error: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.get(url, headers=_headers(), timeout=timeout)
            resp.raise_for_status()
            return resp
        except requests.RequestException as exc:
            last_error = exc
            if attempt < MAX_RETRIES:
                wait = BACKOFF_SECONDS * (2 ** (attempt - 1))
                logger.warning(
                    "request failed (%s), retrying in %.1fs (attempt %d/%d)",
                    exc, wait, attempt, MAX_RETRIES,
                )
                time.sleep(wait)
    raise last_error  # type: ignore[misc] — non-None when loop ends


def fetch_amazon_price(url: str, timeout: int = 15) -> tuple[str, float]:
    """Fetch the product title and current price (INR) from an Amazon.in URL."""
    logger.debug("fetching %s (timeout=%ss)", url, timeout)
    resp = _get_with_retry(url, timeout)

    title_match = re.search(
        r'<span id="productTitle"[^>]*>(.*?)</span>', resp.text, re.S
    )
    title = re.sub(r"\s+", " ", title_match.group(1)).strip() if title_match else ""

    price_match = re.search(r'<span class="a-price-whole">([\d,]+)', resp.text)
    if not price_match:
        raise ValueError("price not found on page (maybe blocked or out of stock)")
    price = float(price_match.group(1).replace(",", ""))

    return title, price
