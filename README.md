# dealwatch

A Telegram price-drop alert bot for Indian e-commerce (Amazon.in, Flipkart). Watches product pages, stores price history in SQLite, and pings you on Telegram when a price hits your target.

Status: early skeleton. Telegram side is not wired up yet — for now it is a CLI that checks prices, keeps history, and draws a chart. Remaining work is tracked in TASKS.md.

## Setup

```bash
git clone <repo-url> dealwatch
cd dealwatch
python -m venv .venv
.venv/Scripts/activate      # windows; use source .venv/bin/activate on linux/mac
pip install -r requirements.txt
```

Then pick a config file:

```bash
cp config.json.example config.json    # or products.json.example products.json
```

`config.json` holds the watched products and is gitignored, so your list stays local:

```json
{
  "timeout": 15,
  "products": [
    { "name": "sony xm4", "url": "https://www.amazon.in/dp/B0EXAMPLE0", "target": 19999 }
  ]
}
```

`timeout` is optional and applies to every product; each product can override it with its own `timeout`. A product `target` makes the run shout when the price drops to that value or below.

Copy `.env.example` to `.env` (also gitignored) and fill in `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` once the bot side lands.

## Usage

```bash
python -m dealwatch.cli                       # check everything in the config once
python -m dealwatch.cli --url <url> --name "x" # check a single product by url
python -m dealwatch.cli --dry-run             # list what would be checked
python -m dealwatch.cli add <url> "name" --target 19999   # add to config.json
python -m dealwatch.cli history "sony"        # price history table
python -m dealwatch.cli history "sony" --csv history.csv # ...or as csv
python -m dealwatch.cli chart "sony" --out sony.html      # html price chart
python -m dealwatch.cli watch --every 30      # loop every 30 min, no cron needed
python -m dealwatch.cli watch --once          # a single round of the loop
python -m dealwatch.cli bot --every 30        # telegram bot: /add, /status, /remove, /history
```

Useful flags: `--db <path>` to point at another sqlite file, `--timeout <sec>` to override the config, `--verbose` for debug logs.

The bot reads `TELEGRAM_BOT_TOKEN` from the environment, and `TELEGRAM_CHAT_ID` if you want alerts in a specific chat (it is remembered from the first message otherwise).

Top-level flags go before the command, e.g. `python -m dealwatch.cli --verbose watch --every 60`.

Stop a watch loop with ctrl-c.

## Tests

```bash
python -m unittest discover -s tests
```
