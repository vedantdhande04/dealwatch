"""dealwatch CLI — check watched products and print current prices."""

import argparse
import csv
import json
import logging
import sys
import time
from datetime import datetime
from pathlib import Path

from dealwatch import chart, db
from dealwatch.fetcher import OutOfStockError, fetch_price

_REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_TIMEOUT = 15
DEFAULT_CONFIG = Path("config.json")

logger = logging.getLogger("dealwatch.cli")


def _stamp(checked_at: str) -> str:
    """Format a stored utc timestamp in the local timezone."""
    try:
        when = datetime.fromisoformat(checked_at).astimezone()
    except ValueError:
        return checked_at
    return when.strftime("%Y-%m-%d %H:%M")


def config_paths() -> tuple[Path, ...]:
    """Where to look for the watched products, in order of preference."""
    return (
        Path("config.json"),
        Path("products.json"),
        _REPO_ROOT / "config.json.example",
        _REPO_ROOT / "products.json.example",
    )


def dedupe_products(products: list[dict]) -> list[dict]:
    """Drop extra copies of a url, keeping the first entry as it appears."""
    seen: set[str] = set()
    unique: list[dict] = []
    for product in products:
        url = str(product.get("url", "")).strip()
        if url and url in seen:
            logger.warning("skipping duplicate url in config: %s", url)
            continue
        if url:
            seen.add(url)
        unique.append(product)
    return unique


def read_config(path: Path) -> dict:
    """Read a config file and normalise it to {"products": [...], "timeout": int|None}.

    Both the new object form ({"timeout": .., "products": [..]}) and a bare
    list of products are accepted.
    """
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, list):
        return {"products": dedupe_products(data), "timeout": None}
    if not isinstance(data, dict):
        raise ValueError(f"{path} should hold an object or a list of products")
    products = data.get("products") or []
    return {"products": dedupe_products(products), "timeout": data.get("timeout")}


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
        "--target", type=float, default=None,
        help="alert when a price drops to this value or below "
             "(overrides the target in the config)",
    )
    parser.add_argument(
        "--json", action="store_true",
        help="print the check results as json",
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

    add = sub.add_parser("add", help="add a product url to the config")
    add.add_argument("url", help="product url")
    add.add_argument(
        "name", nargs="?", default=None,
        help="friendly name (default: the url itself)",
    )
    add.add_argument(
        "--target", type=float, default=None,
        help="alert when the price drops to this value",
    )
    add.add_argument(
        "--config", type=Path, default=DEFAULT_CONFIG,
        help="config file to write (default: config.json)",
    )

    listing = sub.add_parser(
        "list", help="list the watched products from the config"
    )
    listing.add_argument(
        "--db", type=Path, default=argparse.SUPPRESS,
        help="sqlite db path (default: dealwatch.db next to the package)",
    )

    stats = sub.add_parser(
        "stats", help="show the low, high and average price for a product"
    )
    stats.add_argument("product", help="product name or url fragment to look up")
    stats.add_argument(
        "--limit", type=int, default=200,
        help="max checks to look at (default: 200)",
    )
    stats.add_argument(
        "--db", type=Path, default=argparse.SUPPRESS,
        help="sqlite db path (default: dealwatch.db next to the package)",
    )

    chart = sub.add_parser(
        "chart", help="write an html price chart for a product"
    )
    chart.add_argument("product", help="product name or url fragment to look up")
    chart.add_argument(
        "--limit", type=int, default=90,
        help="max checks to chart (default: 90)",
    )
    chart.add_argument(
        "--out", type=Path, default=None,
        help="html file to write (default: <product>-price-chart.html)",
    )
    chart.add_argument(
        "--db", type=Path, default=argparse.SUPPRESS,
        help="sqlite db path (default: dealwatch.db next to the package)",
    )

    watch = sub.add_parser(
        "watch", help="keep checking the watched products on a loop"
    )
    watch.add_argument(
        "--every", type=int, default=30,
        help="minutes to wait between checks (default: 30)",
    )
    watch.add_argument(
        "--once", action="store_true",
        help="do a single check round and exit instead of looping",
    )
    watch.add_argument(
        "--db", type=Path, default=argparse.SUPPRESS,
        help="sqlite db path (default: dealwatch.db next to the package)",
    )

    bot = sub.add_parser(
        "bot", help="run the telegram bot (needs TELEGRAM_BOT_TOKEN)"
    )
    bot.add_argument(
        "--config", type=Path, default=DEFAULT_CONFIG,
        help="config file to read and write (default: config.json)",
    )
    bot.add_argument(
        "--every", type=int, default=30,
        help="minutes between check rounds (default: 30)",
    )
    bot.add_argument(
        "--once", action="store_true",
        help="answer pending commands and check once, then exit",
    )
    bot.add_argument(
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
        stamp = _stamp(row["checked_at"])
        print(f"{row['product'][:29]:<30} {stamp:<22} {'₹' + format(row['price'], ',.0f'):>12}")
    return 0


def run_add(args: argparse.Namespace) -> int:
    """Append a product to the config file, skipping urls already watched."""
    path = Path(args.config)
    if path.exists():
        config = read_config(path)
    else:
        config = {"products": [], "timeout": None}
    products = config["products"]
    url = args.url.strip()
    if any(str(p.get("url", "")).strip() == url for p in products):
        print(f"{url} is already in {path}")
        return 1
    entry: dict = {"name": args.name or url, "url": url}
    if args.target is not None:
        entry["target"] = args.target
    products.append(entry)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"timeout": config["timeout"], "products": products}, f, indent=2)
        f.write("\n")
    print(f"added {entry['name']} to {path}")
    return 0


def run_check(args: argparse.Namespace, notify=None) -> int:
    """Check every watched product once. Returns the number of failed fetches.

    `notify` is an optional callable (name, price, target) used to shout about
    a target hit somewhere else, e.g. a telegram message.
    """
    json_out = getattr(args, "json", False)
    # json mode collects the results instead of printing per-product lines
    say = (lambda *a, **k: None) if json_out else print

    if args.url:
        products = [{"name": args.name or args.url, "url": args.url}]
        config_timeout = None
    else:
        config = load_config()
        products = config["products"]
        config_timeout = config["timeout"]
        if not products:
            logger.warning("no products in config")
            print("[]" if json_out else "no products in config")
            return 1

    db_path = args.db or db.DEFAULT_DB
    results: list[dict] = []
    failures = 0
    for product in products:
        name = product.get("name", product["url"])
        target = args.target if args.target is not None else product.get("target")
        if args.dry_run:
            note = f" (alert at ₹{target:,.0f})" if target else ""
            say(f"{name}: would check {product['url']}{note}")
            if json_out:
                results.append({
                    "name": name, "url": product["url"],
                    "status": "dry-run", "target": target,
                })
            continue
        timeout = args.timeout or product.get("timeout") or config_timeout or DEFAULT_TIMEOUT
        try:
            title, price = fetch_price(product["url"], timeout=timeout)
        except OutOfStockError as exc:
            logger.info("%s is out of stock: %s", name, exc)
            say(f"{name}: out of stock — {exc}")
            if json_out:
                results.append({
                    "name": name, "url": product["url"],
                    "status": "out-of-stock", "error": str(exc),
                })
            continue
        except Exception as exc:  # noqa: BLE001 — report and move on
            logger.error("failed to fetch %s: %s", name, exc)
            say(f"{name}: error — {exc}")
            if json_out:
                results.append({
                    "name": name, "url": product["url"],
                    "status": "error", "error": str(exc),
                })
            failures += 1
            continue
        previous = db.last_price(product["url"], db_path=db_path)
        drop = None
        if previous is not None and price < previous:
            drop = (previous - price) / previous * 100
            say(f"{name}: PRICE DROP — ₹{previous:,.0f} → ₹{price:,.0f} ({drop:.0f}% off)")
        hit = bool(target and price <= target)
        if hit:
            say(f"{name}: TARGET HIT — ₹{price:,.0f} is at or below ₹{target:,.0f}")
            if notify is not None:
                try:
                    notify(name, price, target)
                except Exception as exc:  # noqa: BLE001 — a failed alert is not fatal
                    logger.error("alert failed for %s: %s", name, exc)
        db.record_check(name, product["url"], title, price, db_path=db_path)
        say(f"{name}: {title} — ₹{price:,.0f}")
        if json_out:
            results.append({
                "name": name, "url": product["url"], "status": "ok",
                "title": title, "price": price, "previous": previous,
                "drop_pct": round(drop, 2) if drop is not None else None,
                "target": target, "target_hit": hit,
            })
    if json_out:
        print(json.dumps(results, indent=2))
    return failures


def run_list(args: argparse.Namespace) -> int:
    """Print the watched products and their last stored price, no fetching."""
    products = load_config()["products"]
    if not products:
        print("no products in config")
        return 1
    db_path = args.db or db.DEFAULT_DB
    print(f"{'name':<34} {'target':>10} {'last seen':>12}")
    print("-" * 60)
    for product in products:
        name = str(product.get("name") or product["url"])
        target = product.get("target")
        target_text = f"₹{target:,.0f}" if target else "-"
        last = db.last_price(product["url"], db_path=db_path)
        price_text = f"₹{last:,.0f}" if last is not None else "-"
        print(f"{name[:33]:<34} {target_text:>10} {price_text:>12}")
    return 0


def run_stats(args: argparse.Namespace) -> int:
    """Print the low, high and average price a product has been checked at."""
    db_path = args.db or db.DEFAULT_DB
    rows = db.history(args.product, db_path=db_path, limit=args.limit)
    if not rows:
        print(f"no price history found for {args.product!r}")
        return 1
    prices = [row["price"] for row in rows]
    lowest = min(rows, key=lambda row: row["price"])
    highest = max(rows, key=lambda row: row["price"])
    label = rows[-1]["product"] or args.product
    print(f"{label}: {len(prices)} checks")
    print(f"lowest   ₹{lowest['price']:,.0f}  ({_stamp(lowest['checked_at'])})")
    print(f"highest  ₹{highest['price']:,.0f}  ({_stamp(highest['checked_at'])})")
    print(f"average  ₹{sum(prices) / len(prices):,.0f}")
    print(f"latest   ₹{prices[-1]:,.0f}  ({_stamp(rows[-1]['checked_at'])})")
    if highest["price"] > 0:
        off = (highest["price"] - lowest["price"]) / highest["price"] * 100
        print(f"swing    down {off:.0f}% from the highest")
    return 0


def run_chart(args: argparse.Namespace) -> int:
    """Write an html chart of a product's stored prices."""
    db_path = args.db or db.DEFAULT_DB
    rows = db.history(args.product, db_path=db_path, limit=args.limit)
    if not rows:
        print(f"no price history found for {args.product!r}")
        return 1
    label = rows[-1]["product"] or args.product
    out = args.out or Path(f"{chart.slugify(label)}-price-chart.html")
    out.write_text(chart.render_html(label, rows), encoding="utf-8")
    print(f"wrote a chart for {len(rows)} checks to {out}")
    return 0


def run_watch(args: argparse.Namespace) -> int:
    """Check the products over and over, sleeping --every minutes between rounds."""
    every = max(1, args.every)
    rounds = 0
    while True:
        rounds += 1
        logger.info("starting check round %d", rounds)
        run_check(args)
        if getattr(args, "once", False):
            logger.info("--once given, exiting after round %d", rounds)
            return 0
        print(f"round {rounds} done — next check in {every} min (ctrl-c to stop)")
        try:
            time.sleep(every * 60)
        except KeyboardInterrupt:
            print("stopped watching")
            return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )

    if args.command == "history":
        return run_history(args)

    if args.command == "add":
        return run_add(args)

    if args.command == "list":
        return run_list(args)

    if args.command == "stats":
        return run_stats(args)

    if args.command == "chart":
        return run_chart(args)

    if args.command == "watch":
        try:
            return run_watch(args)
        except KeyboardInterrupt:
            print("stopped watching")
            return 0

    if args.command == "bot":
        from dealwatch import telegram  # imported here so the cli stays light

        try:
            return telegram.run_bot(
                config_path=args.config,
                db_path=args.db or db.DEFAULT_DB,
                every=args.every,
                once=args.once,
            )
        except KeyboardInterrupt:
            print("bot stopped")
            return 0

    return 1 if run_check(args) else 0


if __name__ == "__main__":
    sys.exit(main())
