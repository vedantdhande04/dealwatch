"""dealwatch CLI — check watched products and print current prices."""

import argparse
import json
import logging
import sys
from pathlib import Path

from dealwatch import db
from dealwatch.fetcher import fetch_amazon_price

DEFAULT_CONFIG = Path(__file__).resolve().parent.parent / "products.json.example"
DEFAULT_TIMEOUT = 15

logger = logging.getLogger("dealwatch.cli")


def load_products() -> list[dict]:
    cfg = Path("products.json")
    if not cfg.exists():
        cfg = DEFAULT_CONFIG
    with open(cfg, encoding="utf-8") as f:
        return json.load(f)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="dealwatch", description=__doc__)
    parser.add_argument(
        "--dry-run", action="store_true",
        help="list products without fetching anything",
    )
    parser.add_argument(
        "--timeout", type=int, default=None,
        help="HTTP timeout in seconds (overrides per-product config)",
    )
    parser.add_argument(
        "--db", type=Path, default=None,
        help="sqlite db path (default: dealwatch.db next to the package)",
    )
    parser.add_argument(
        "--verbose", action="store_true", help="enable debug logging"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )

    products = load_products()
    if not products:
        logger.warning("no products in config")
        print("no products in config")
        return 1

    db_path = args.db or db.DEFAULT_DB
    for product in products:
        name = product.get("name", product["url"])
        if args.dry_run:
            print(f"{name}: would check {product['url']}")
            continue
        timeout = args.timeout or product.get("timeout") or DEFAULT_TIMEOUT
        try:
            title, price = fetch_amazon_price(product["url"], timeout=timeout)
            previous = db.last_price(product["url"], db_path=db_path)
            if previous is not None and price < previous:
                drop = (previous - price) / previous * 100
                print(f"{name}: PRICE DROP — ₹{previous:,.0f} → ₹{price:,.0f} ({drop:.0f}% off)")
            db.record_check(name, product["url"], title, price, db_path=db_path)
            print(f"{name}: {title} — ₹{price:,.0f}")
        except Exception as exc:  # noqa: BLE001 — report and move on
            logger.error("failed to fetch %s: %s", name, exc)
            print(f"{name}: error — {exc}")
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
