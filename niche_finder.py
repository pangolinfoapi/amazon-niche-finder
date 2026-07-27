#!/usr/bin/env python3
"""Amazon Niche Finder — discover blue-ocean niches with low competition.

Uses the Pangolinfo MCP endpoint (https://mcp.pangolinfo.com/mcp) — the same
Model Context Protocol server AI assistants use — to call ``filter_niches``
against real Amazon demand/competition data, scores each niche for
"blue-ocean" opportunity, and stores the results in SQLite so you can watch
opportunities accumulate over time.

Zero dependencies: Python 3.10+ standard library only.

Commands:
    init      Create niches.json from the example file
    run       Query filter_niches for every configured query and store results
    history   Print tracked niches (optionally filtered by query label)
    report    Generate a Markdown report of the top opportunities

Get a free API key (200 free calls) at https://tool.pangolinfo.com
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sqlite3
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CONFIG_FILE = ROOT / "niches.json"
EXAMPLE_FILE = ROOT / "niches.example.json"
DB_FILE = ROOT / "data" / "niches.db"
CSV_FILE = ROOT / "data" / "opportunities.csv"
REPORTS_DIR = ROOT / "reports"

MCP_URL = os.environ.get("PANGOLIN_MCP_URL", "https://mcp.pangolinfo.com/mcp")
MCP_PROTOCOL_VERSION = "2024-11-05"

# The MCP endpoint sits behind Cloudflare, which blocks default library
# signatures (python-urllib gets Error 1010). Present a browser UA.
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS niches (
  niche_key TEXT PRIMARY KEY,
  query_label TEXT NOT NULL,
  marketplace_id TEXT NOT NULL,
  niche_id TEXT NOT NULL,
  niche_title TEXT,
  search_volume_t90 INTEGER,
  search_volume_t360 INTEGER,
  minimum_price REAL,
  maximum_price REAL,
  return_rate_t360 REAL,
  product_count INTEGER,
  brand_count INTEGER,
  selling_partner_count_t360 INTEGER,
  avg_review_rating REAL,
  avg_best_seller_rank INTEGER,
  avg_review_count INTEGER,
  score REAL,
  is_opportunity INTEGER DEFAULT 0,
  first_seen_at TEXT,
  last_seen_at TEXT
);
"""

# Metric fields we persist, in (db_column, candidate_keys...) form.
METRIC_FIELDS = [
    ("search_volume_t90", "searchVolumeT90", "search_volume_t90"),
    ("search_volume_t360", "searchVolumeT360", "search_volume_t360"),
    ("minimum_price", "minimumPrice", "minimum_price"),
    ("maximum_price", "maximumPrice", "maximum_price"),
    ("return_rate_t360", "returnRateT360", "return_rate_t360"),
    ("product_count", "productCount", "product_count"),
    ("brand_count", "brandCount", "brand_count"),
    ("selling_partner_count_t360", "sellingPartnerCountT360", "selling_partner_count_t360"),
    ("avg_review_rating", "avgReviewRating", "avg_review_rating"),
    ("avg_best_seller_rank", "avgBestSellerRank", "avg_best_seller_rank"),
    ("avg_review_count", "avgReviewCount", "avg_review_count"),
]


# --------------------------------------------------------------------------- #
# Minimal MCP (streamable-HTTP) client — stdlib only
# --------------------------------------------------------------------------- #

class McpError(RuntimeError):
    pass


class McpClient:
    """Talks JSON-RPC to the Pangolinfo MCP server over streamable-HTTP."""

    def __init__(self, token: str, url: str = MCP_URL, timeout: int = 90) -> None:
        self.token = token
        self.url = url
        self.timeout = timeout
        self.session_id: str | None = None
        self._next_id = 0
        self._initialized = False

    def _post(self, payload: dict) -> dict:
        req = urllib.request.Request(self.url, data=json.dumps(payload).encode(), method="POST")
        req.add_header("Content-Type", "application/json")
        req.add_header("Accept", "application/json, text/event-stream")
        req.add_header("User-Agent", USER_AGENT)
        req.add_header("Authorization", f"Bearer {self.token}")
        if self.session_id:
            req.add_header("mcp-session-id", self.session_id)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                sid = resp.headers.get("mcp-session-id")
                if sid:
                    self.session_id = sid
                raw = resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            raise McpError(f"HTTP {exc.code} from MCP server: {exc.read()[:200]!r}") from exc
        except urllib.error.URLError as exc:
            raise McpError(f"MCP server unreachable: {exc}") from exc
        if not raw.strip():
            return {}
        for line in raw.splitlines():
            line = line.strip()
            if line.startswith("data:"):
                return json.loads(line[5:].strip())
        return json.loads(raw)

    def initialize(self) -> None:
        self._next_id += 1
        self._post({
            "jsonrpc": "2.0", "id": self._next_id, "method": "initialize",
            "params": {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "amazon-niche-finder", "version": "1.0.0"},
            },
        })
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})
        self._initialized = True

    def call_tool(self, name: str, arguments: dict) -> dict:
        if not self._initialized:
            self.initialize()
        self._next_id += 1
        resp = self._post({
            "jsonrpc": "2.0", "id": self._next_id, "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        })
        if "error" in resp:
            raise McpError(f"JSON-RPC error: {resp['error']}")
        result = resp.get("result", {})
        texts = [c.get("text", "") for c in result.get("content", []) if c.get("type") == "text"]
        if result.get("isError"):
            raise McpError("tool error: " + (" ".join(texts)[:300] or "unknown"))
        for text in texts:
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                continue
        return {}


# --------------------------------------------------------------------------- #
# Response parsing
# --------------------------------------------------------------------------- #

def _looks_like_niche(d: dict) -> bool:
    keys = {k.lower() for k in d.keys()}
    has_id = bool(keys & {"nicheid", "niche_id", "id", "nichetitle", "niche_title", "title"})
    has_metric = bool(keys & {"searchvolume", "search_volume", "searchvolumet90",
                              "searchvolumet360", "price", "minimumprice", "minimum_price"})
    return has_id and has_metric


def _find_niche_list(node, depth: int = 0) -> list:
    if depth > 6 or node is None:
        return []
    if isinstance(node, dict):
        for value in node.values():
            if isinstance(value, list) and value and isinstance(value[0], dict) and _looks_like_niche(value[0]):
                return value
        for value in node.values():
            found = _find_niche_list(value, depth + 1)
            if found:
                return found
    if isinstance(node, list):
        for value in node:
            found = _find_niche_list(value, depth + 1)
            if found:
                return found
    return []


def extract_niche_list(payload: dict) -> list:
    """Pull the list of niche dicts out of a filter_niches response."""
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("data", "results", "niches", "items"):
            v = payload.get(key)
            if isinstance(v, list) and v and isinstance(v[0], dict):
                return v
        return _find_niche_list(payload)
    return []


def _get(d: dict, *names):
    low = {k.lower(): v for k, v in d.items()}
    for n in names:
        if n.lower() in low and low[n.lower()] not in (None, ""):
            return low[n.lower()]
    return None


def _num(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_niche(item: dict) -> dict:
    out = {"niche_id": _get(item, "nicheId", "niche_id", "id"),
           "niche_title": _get(item, "nicheTitle", "niche_title", "title", "name")}
    for column, *keys in METRIC_FIELDS:
        out[column] = _num(_get(item, *keys))
    return out


# --------------------------------------------------------------------------- #
# Opportunity scoring
# --------------------------------------------------------------------------- #

def blue_ocean_score(search_volume: float | None, competition: float | None) -> float:
    """Higher = more demand per unit of competition (classic blue-ocean proxy)."""
    demand = search_volume or 0.0
    comp = competition or 0.0
    return demand / (comp + 1.0)


def is_opportunity(metrics: dict, opp: dict) -> bool:
    sv = metrics.get("search_volume_t90") or 0
    if opp.get("min_search_volume_t90") and sv < opp["min_search_volume_t90"]:
        return False
    price = metrics.get("minimum_price")
    if opp.get("min_price") is not None and price is not None and price < opp["min_price"]:
        return False
    if opp.get("max_price") is not None and price is not None and price > opp["max_price"]:
        return False
    comp = metrics.get("brand_count") or metrics.get("selling_partner_count_t360") or 0
    if opp.get("max_brand_count") and comp > opp["max_brand_count"]:
        return False
    return True


# --------------------------------------------------------------------------- #
# Storage
# --------------------------------------------------------------------------- #

def db_connect() -> sqlite3.Connection:
    DB_FILE.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_FILE)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    return conn


def upsert_niche(conn: sqlite3.Connection, row: dict, now: str) -> None:
    conn.execute(
        """INSERT INTO niches
           (niche_key, query_label, marketplace_id, niche_id, niche_title,
            search_volume_t90, search_volume_t360, minimum_price, maximum_price,
            return_rate_t360, product_count, brand_count, selling_partner_count_t360,
            avg_review_rating, avg_best_seller_rank, avg_review_count,
            score, is_opportunity, first_seen_at, last_seen_at)
           VALUES (:niche_key, :query_label, :marketplace_id, :niche_id, :niche_title,
                   :search_volume_t90, :search_volume_t360, :minimum_price, :maximum_price,
                   :return_rate_t360, :product_count, :brand_count, :selling_partner_count_t360,
                   :avg_review_rating, :avg_best_seller_rank, :avg_review_count,
                   :score, :is_opportunity, :first_seen_at, :last_seen_at)
           ON CONFLICT(niche_key) DO UPDATE SET
             niche_title=excluded.niche_title,
             search_volume_t90=excluded.search_volume_t90,
             search_volume_t360=excluded.search_volume_t360,
             minimum_price=excluded.minimum_price,
             maximum_price=excluded.maximum_price,
             return_rate_t360=excluded.return_rate_t360,
             product_count=excluded.product_count,
             brand_count=excluded.brand_count,
             selling_partner_count_t360=excluded.selling_partner_count_t360,
             avg_review_rating=excluded.avg_review_rating,
             avg_best_seller_rank=excluded.avg_best_seller_rank,
             avg_review_count=excluded.avg_review_count,
             score=excluded.score,
             is_opportunity=excluded.is_opportunity,
             last_seen_at=excluded.last_seen_at""",
        row,
    )
    conn.commit()


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #

def cmd_init() -> None:
    if CONFIG_FILE.exists():
        sys.exit("niches.json already exists — edit it directly.")
    CONFIG_FILE.write_text(EXAMPLE_FILE.read_text(), encoding="utf-8")
    print(f"Created {CONFIG_FILE.name} — adjust the marketplace/filters to your needs.")


def cmd_run(args) -> None:
    if not CONFIG_FILE.exists():
        sys.exit("niches.json not found. Run: python niche_finder.py init")
    queries = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    token = os.environ.get("PANGOLIN_TOKEN") or os.environ.get("PANGOLINFO_API_KEY")
    if not token:
        sys.exit("Set PANGOLIN_TOKEN env var (free key: https://tool.pangolinfo.com)")

    conn = db_connect()
    client = McpClient(token=token)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    total, stored, opp_count = 0, 0, 0

    for query in queries:
        label = query.get("label", query.get("marketplace_id", "query"))
        marketplace_id = query["marketplace_id"]
        filters = query.get("filters", {})
        opp = query.get("opportunity", {})
        arguments = {"marketplace_id": marketplace_id}
        arguments.update({k: v for k, v in filters.items() if v is not None})

        total += 1
        try:
            payload = client.call_tool("filter_niches", arguments)
        except McpError as exc:
            print(f"  ! {label}: {exc}")
            time.sleep(args.delay)
            continue

        niches = extract_niche_list(payload)
        if not niches:
            print(f"  · {label}: no niches returned")
            time.sleep(args.delay)
            continue

        for item in niches:
            metrics = parse_niche(item)
            if not metrics.get("niche_id"):
                continue
            competition = metrics.get("brand_count") or metrics.get("selling_partner_count_t360") or 0
            score = blue_ocean_score(metrics.get("search_volume_t90"), competition)
            opp_flag = 1 if is_opportunity(metrics, opp) else 0
            row = {
                "niche_key": f"{label}|{metrics['niche_id']}",
                "query_label": label,
                "marketplace_id": marketplace_id,
                "niche_id": str(metrics["niche_id"]),
                "niche_title": metrics.get("niche_title"),
                "search_volume_t90": metrics.get("search_volume_t90"),
                "search_volume_t360": metrics.get("search_volume_t360"),
                "minimum_price": metrics.get("minimum_price"),
                "maximum_price": metrics.get("maximum_price"),
                "return_rate_t360": metrics.get("return_rate_t360"),
                "product_count": metrics.get("product_count"),
                "brand_count": metrics.get("brand_count"),
                "selling_partner_count_t360": metrics.get("selling_partner_count_t360"),
                "avg_review_rating": metrics.get("avg_review_rating"),
                "avg_best_seller_rank": metrics.get("avg_best_seller_rank"),
                "avg_review_count": metrics.get("avg_review_count"),
                "score": round(score, 2),
                "is_opportunity": opp_flag,
                "first_seen_at": now,
                "last_seen_at": now,
            }
            upsert_niche(conn, row, now)
            stored += 1
            opp_count += opp_flag
            tag = "★ OPP" if opp_flag else "  "
            print(f"  {tag} {label}: {metrics.get('niche_title')} "
                  f"(sv90={metrics.get('search_volume_t90')}, score={row['score']})")
        time.sleep(args.delay)

    print(f"\nDone: stored {stored} niches across {total} queries "
          f"({opp_count} flagged as opportunities). DB: {DB_FILE.relative_to(ROOT)}")


def cmd_history(args) -> None:
    conn = db_connect()
    query = "SELECT query_label, niche_id, niche_title, search_volume_t90, brand_count, score, is_opportunity, first_seen_at FROM niches"
    params = []
    if args.label:
        query += " WHERE query_label = ?"
        params.append(args.label)
    query += " ORDER BY score DESC LIMIT ?"
    params.append(args.limit)
    rows = conn.execute(query, params).fetchall()
    if not rows:
        print("No data yet. Run: python niche_finder.py run")
        return
    print(f"{'query':<22}{'niche':<14}{'title':<28}{'sv90':>8}{'brands':>7}{'score':>9}{'opp':>4}")
    print("-" * 92)
    for label, nid, title, sv, brands, score, opp, seen in rows:
        print(f"{label[:21]:<22}{str(nid)[:13]:<14}{(title or '')[:27]:<28}"
              f"{sv if sv is not None else '-':>8}{brands if brands is not None else '-':>7}"
              f"{score:>9}{'★' if opp else '':>4}")


def cmd_report(_args) -> None:
    conn = db_connect()
    REPORTS_DIR.mkdir(exist_ok=True)
    today = datetime.now(timezone.utc).date().isoformat()

    rows = conn.execute(
        """SELECT query_label, niche_id, niche_title, search_volume_t90, minimum_price,
                  maximum_price, brand_count, score
           FROM niches WHERE is_opportunity = 1 ORDER BY score DESC LIMIT 200"""
    ).fetchall()
    if not rows:
        print("No opportunities tracked yet. Run: python niche_finder.py run")
        return

    lines = [
        f"# Amazon Niche Opportunities — {today}",
        "",
        "Discovered with [amazon-niche-finder](https://github.com/pangolinfoapi/amazon-niche-finder) "
        "using the [Pangolinfo Niche Data API](https://www.pangolinfo.com/amazon-niche-data-api/).",
        "",
        "> Blue-ocean score = search volume (T90) ÷ (brand count + 1). Higher = more demand per unit of competition.",
        "",
        "## Top opportunities",
        "",
        "| Query | Niche | Search vol (90d) | Price range | Brands | Score |",
        "|---|---|---|---|---|---|",
    ]
    for label, nid, title, sv, pmin, pmax, brands, score in rows:
        price = f"{pmin}-{pmax}" if pmin is not None or pmax is not None else "—"
        lines.append(f"| {label} | {title or nid} | {sv if sv is not None else '—'} | {price} | "
                     f"{brands if brands is not None else '—'} | {score} |")

    report = "\n".join(lines) + "\n"
    (REPORTS_DIR / f"{today}.md").write_text(report, encoding="utf-8")
    (REPORTS_DIR / "latest.md").write_text(report, encoding="utf-8")

    CSV_FILE.parent.mkdir(exist_ok=True)
    with CSV_FILE.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["query_label", "niche_id", "niche_title", "search_volume_t90",
                         "minimum_price", "maximum_price", "brand_count", "score"])
        writer.writerows(rows)
    print(f"Report written: reports/{today}.md, reports/latest.md, data/opportunities.csv")


def main() -> None:
    parser = argparse.ArgumentParser(description="Amazon Niche Finder (powered by Pangolinfo)")
    parser.add_argument("--delay", type=float, default=2.0, help="Seconds between API calls (default: 2)")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init", help="Create niches.json from the example")
    sub.add_parser("run", help="Query filter_niches for all configured queries")
    hist = sub.add_parser("history", help="Show tracked niches")
    hist.add_argument("--label")
    hist.add_argument("--limit", type=int, default=50)
    sub.add_parser("report", help="Generate Markdown + CSV reports")

    args = parser.parse_args()
    {"init": cmd_init, "run": cmd_run, "history": cmd_history, "report": cmd_report}[args.command](args)


if __name__ == "__main__":
    main()
