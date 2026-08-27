"""Fetch prices from Indian e-commerce product pages."""

import re

import requests

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    )
}


def fetch_amazon_price(url: str, timeout: int = 15) -> tuple[str, float]:
    """Fetch the product title and current price (INR) from an Amazon.in URL."""
    resp = requests.get(url, headers=HEADERS, timeout=timeout)
    resp.raise_for_status()

    title_match = re.search(
        r'<span id="productTitle"[^>]*>(.*?)</span>', resp.text, re.S
    )
    title = re.sub(r"\s+", " ", title_match.group(1)).strip() if title_match else ""

    price_match = re.search(r'<span class="a-price-whole">([\d,]+)', resp.text)
    if not price_match:
        raise ValueError("price not found on page (maybe blocked or out of stock)")
    price = float(price_match.group(1).replace(",", ""))

    return title, price
