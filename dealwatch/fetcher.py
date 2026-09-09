"""Fetch prices from Indian e-commerce product pages."""

import json
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

_AMAZON_TITLE = re.compile(r'<span id="productTitle"[^>]*>(.*?)</span>', re.S)
_AMAZON_PRICE = re.compile(r'<span class="a-price-whole">([\d,]+)')
_AMAZON_AVAILABILITY = re.compile(
    r'id="availability"[^>]*>\s*<span[^>]*>(.*?)</span>', re.S
)
_AMAZON_OOS_TEXT = re.compile(
    r"currently unavailable|temporarily out of stock|out of stock|sold out", re.I
)

# flipkart embedded-state schema markers (schema.org availability values)
_FK_OOS = ("OutOfStock", "SoldOut", "Discontinued")

_OG_TITLE = re.compile(r'<meta property="og:title" content="([^"]+)"', re.I)
_WS = re.compile(r"\s+")

# old flipkart markup, in case the embedded state ever goes away
_FK_TITLE = re.compile(r'<span class="[^"]*B_NuCI[^"]*"[^>]*>(.*?)</span>', re.S)
_FK_PRICE = re.compile(
    r'class="[^"]*_30jeq3[^"]*"[^>]*>\s*(?:₹|Rs\.?|&#8377;)?\s*([\d,]+)'
)


class OutOfStockError(Exception):
    """The page loaded but the item is not available to buy right now."""


class PriceNotFoundError(ValueError):
    """The page loaded but no buyable price could be found on it."""


def _headers() -> dict[str, str]:
    """Fresh headers with a randomly picked user agent."""
    return {"User-Agent": random.choice(USER_AGENTS)}


def _clean(text: str) -> str:
    """Collapse whitespace in scraped text."""
    return _WS.sub(" ", text).strip()


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


def _initial_state(html: str) -> dict | None:
    """Return flipkart's embedded __INITIAL_STATE__ JSON, if parseable."""
    match = re.search(r"window\.__INITIAL_STATE__\s*=\s*", html)
    if not match:
        return None
    body = html[match.end():].split("</script>", 1)[0].strip().rstrip(";").strip()
    try:
        data = json.loads(body)
        return data if isinstance(data, dict) else None
    except (json.JSONDecodeError, ValueError):
        return None


def parse_flipkart_page(html: str) -> tuple[str, float]:
    """Extract (title, price in INR) from a Flipkart product page.

    Modern flipkart embeds a schema.org product record in its
    __INITIAL_STATE__ blob; fall back to the old markup if that
    ever goes away.
    """
    product: dict | None = None
    state = _initial_state(html)
    if state:
        try:
            schemas = (
                state.get("multiWidgetState", {})
                .get("pageDataResponse", {})
                .get("seoData", {})
                .get("schema", [])
            )
            for entry in schemas:
                if isinstance(entry, dict) and entry.get("@type") == "Product":
                    product = entry
                    break
        except AttributeError:
            product = None

    if product:
        offers = product.get("offers") or {}
        if isinstance(offers, list):
            offers = offers[0] if offers else {}
        availability = str(offers.get("availability", ""))
        if any(marker in availability for marker in _FK_OOS):
            raise OutOfStockError("item is out of stock on flipkart")
        price = offers.get("price")
        if price is None:
            raise PriceNotFoundError(
                "price not found on page (maybe blocked or out of stock)"
            )
        name = product.get("name") or ""
        return str(name).strip(), float(price)

    # old markup fallback
    title_match = _FK_TITLE.search(html)
    if title_match:
        title = _clean(title_match.group(1))
    else:
        og = _OG_TITLE.search(html)
        title = _clean(og.group(1)) if og else ""
    price_match = _FK_PRICE.search(html)
    if not price_match:
        if re.search(r"sold out|out of stock", html, re.I):
            raise OutOfStockError("item is out of stock on flipkart")
        raise PriceNotFoundError(
            "price not found on page (maybe blocked or out of stock)"
        )
    return title, float(price_match.group(1).replace(",", ""))


def parse_amazon_page(html: str) -> tuple[str, float]:
    """Extract (title, price in INR) from an Amazon.in product page."""
    availability = _AMAZON_AVAILABILITY.search(html)
    if availability and _AMAZON_OOS_TEXT.search(availability.group(1)):
        raise OutOfStockError("item is unavailable on amazon")

    title_match = _AMAZON_TITLE.search(html)
    title = _clean(title_match.group(1)) if title_match else ""

    price_match = _AMAZON_PRICE.search(html)
    if not price_match:
        raise PriceNotFoundError(
            "price not found on page (maybe blocked or out of stock)"
        )
    return title, float(price_match.group(1).replace(",", ""))


def fetch_amazon_price(url: str, timeout: int = 15) -> tuple[str, float]:
    """Fetch the product title and current price (INR) from an Amazon.in URL."""
    logger.debug("fetching %s (timeout=%ss)", url, timeout)
    return parse_amazon_page(_get_with_retry(url, timeout).text)


def fetch_flipkart_price(url: str, timeout: int = 15) -> tuple[str, float]:
    """Fetch the product title and current price (INR) from a Flipkart URL."""
    logger.debug("fetching %s (timeout=%ss)", url, timeout)
    return parse_flipkart_page(_get_with_retry(url, timeout).text)


def fetch_price(url: str, timeout: int = 15) -> tuple[str, float]:
    """Pick the right site parser for a product url."""
    if "flipkart.com" in url:
        return fetch_flipkart_price(url, timeout)
    return fetch_amazon_price(url, timeout)
