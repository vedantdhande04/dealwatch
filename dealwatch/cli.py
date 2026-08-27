"""dealwatch CLI — check watched products and print current prices."""

import json
import sys
from pathlib import Path

from dealwatch.fetcher import fetch_amazon_price

DEFAULT_CONFIG = Path(__file__).resolve().parent.parent / "products.json.example"


def load_products() -> list[dict]:
    cfg = Path("products.json")
    if not cfg.exists():
        cfg = DEFAULT_CONFIG
    with open(cfg, encoding="utf-8") as f:
        return json.load(f)


def main() -> int:
    products = load_products()
    if not products:
        print("no products in config")
        return 1

    for product in products:
        name = product.get("name", product["url"])
        try:
            title, price = fetch_amazon_price(product["url"])
            print(f"{name}: {title} — ₹{price:,.0f}")
        except Exception as exc:  # noqa: BLE001 — report and move on
            print(f"{name}: error — {exc}")
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
