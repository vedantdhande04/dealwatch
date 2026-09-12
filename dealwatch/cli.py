"""dealwatch CLI — check watched products and print current prices."""

import argparse
import csv
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

from dealwatch import db
from dealwatch.fetcher import OutOfStockError, fetch_price

_REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_TIMEOUT = 15

logger = logging.getLogger("dealwatch.cli")


def config_paths() -> tuple[Path, ...]:
    """Where to look for the watched products, in order of preference."""
    return (
        Path("config.json"),
        Path("products.json"),
        _REPO_ROOT / "config.json.example",
        _REPO_ROOT / "products.json.example",
    )


def read_config(path: Path) -> dict:
    """Read a config file and normalise it to {"products": [...], "timeout": int|None}.

    Both the new object form ({"timeout": .., "products": [..]}) and a bare
    list of products are accepted.
    """
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, list):
        return {"products": data, "timeout": None}
    if not isinstance(data, dict):
        raise ValueError(f"{path} should hold an object or a list of products")
    return {"products": data.get("products") or [], "timeout": data.get("timeout")}


def load_config() -> dict:
    """Find the watched products: config.json, else products.json, else an example."""
    for candidate in config_paths():
        if candidate.exists():
            logger.debug("using config %s", candidate)
            return read_config(candidate)
    raise FileNotFoundError("no config.json or products.json found")


def load_products() -> list[dict]:
    return load_config()["products"]


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
        "--url", default=None,
        help="check a single product url instead of everything in the config",
    )
    parser.add_argument(
        "--name", default=None,
        help="product name to store for the --url check (default: the url)",
    )
    parser.add_argument(
        "--verbose", action="store_true", help="enable debug logging"
    )

    sub = parser.add_subparsers(dest="command", metavar="COMMAND")
    history = sub.add_parser("history", help="print the price history of a product")
    history.add_argument(
        "product", help="product name or url fragment to look up"
    )
    history.add_argument(
        "--limit", type=int, default=30,
        help="max rows to show (default: 30)",
    )
    history.add_argument(
        "--csv", type=Path, default=None,
        help="write the history to this csv file instead of a table",
    )
    history.add_argument(
        "--db", type=Path, default=argparse.SUPPRESS,
        help="sqlite db path (default: dealwatch.db next to the package)",
    )
    return parser


def run_history(args: argparse.Namespace) -> int:
    """Print a price history table for one product."""
    db_path = args.db or db.DEFAULT_DB
    rows = db.history(args.product, db_path=db_path, limit=args.limit)
    if not rows:
        print(f"no price history found for {args.product!r}")
        return 1
    if args.csv:
        with open(args.csv, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["product", "url", "title", "price", "checked_at"])
            for row in rows:
                writer.writerow([
                    row["product"], row["url"], row["title"],
                    row["price"], row["checked_at"],
                ])
        print(f"wrote {len(rows)} rows to {args.csv}")
        return 0
    print(f"{'product':<30} {'checked at':<22} {'price':>12}")
    print("-" * 66)
    for row in rows:
        when = datetime.fromisoformat(row["checked_at"]).astimezone()
        stamp = when.strftime("%Y-%m-%d %H:%M")
        print(f"{row['product'][:29]:<30} {stamp:<22} {'₹' + format(row['price'], ',.0f'):>12}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )

    if args.command == "history":
        return run_history(args)

    if args.url:
        products = [{"name": args.name or args.url, "url": args.url}]
        config_timeout = None
    else:
        config = load_config()
        products = config["products"]
        config_timeout = config["timeout"]
        if not products:
            logger.warning("no products in config")
            print("no products in config")
            return 1

    db_path = args.db or db.DEFAULT_DB
    failures = 0
    for product in products:
        name = product.get("name", product["url"])
        if args.dry_run:
            print(f"{name}: would check {product['url']}")
            continue
        timeout = args.timeout or product.get("timeout") or config_timeout or DEFAULT_TIMEOUT
        try:
            title, price = fetch_price(product["url"], timeout=timeout)
        except OutOfStockError as exc:
            logger.info("%s is out of stock: %s", name, exc)
            print(f"{name}: out of stock — {exc}")
            continue
        except Exception as exc:  # noqa: BLE001 — report and move on
            logger.error("failed to fetch %s: %s", name, exc)
            print(f"{name}: error — {exc}")
            failures += 1
            continue
        previous = db.last_price(product["url"], db_path=db_path)
        if previous is not None and price < previous:
            drop = (previous - price) / previous * 100
            print(f"{name}: PRICE DROP — ₹{previous:,.0f} → ₹{price:,.0f} ({drop:.0f}% off)")
        db.record_check(name, product["url"], title, price, db_path=db_path)
        print(f"{name}: {title} — ₹{price:,.0f}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
