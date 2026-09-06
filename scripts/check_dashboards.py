"""Check every Grafana panel draws something (T-081, docs/13 §5).

    uv run python scripts/check_dashboards.py

A dashboard is a claim that somebody can see what is happening. The way to test
that claim is to run every panel's query and see whether anything comes back:
Prometheus had been scraping `/metrics` on eighteen services and getting a 404
from every one, and six dashboards drew six empty graphs without complaining.

Panels whose queries are legitimately empty are named rather than counted as
failures. An error panel with data in it means something is wrong; an error
panel with nothing in it is the platform working.
"""

from __future__ import annotations

import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DASHBOARDS = ROOT / "infra" / "observability" / "dashboards"
PROMETHEUS = "http://localhost:9090/api/v1/query"

#: Panels that are empty when the platform is healthy. Each is an error or a
#: denial count, and each is named here so an empty one is a deliberate pass
#: rather than a hole nobody noticed.
QUIET_WHEN_WELL = {
    "Errors by kind",
    "Schema violations as a share of calls",
    "Denied tool calls by agent",
}


#: Metric names inside a PromQL expression. Crude on purpose: it only has to
#: find the series a panel reads, and every metric in this platform is prefixed.
_METRIC = re.compile(r"\bcio_[a-z0-9_]+\b")


def series_exist(expr: str) -> bool:
    """Whether the metrics a panel reads have ever been recorded.

    A panel over `rate(...[10m])` draws nothing on an idle platform, which is
    the platform being idle rather than the dashboard being broken. What must
    never happen is a panel whose series does not exist at all: that is a
    dashboard nobody can see anything through, which is what six of them were
    until every service started serving `/metrics`.

    A counter that has never been incremented has no series either, so run this
    after something has actually deliberated. `verify_phase.sh P8` puts it last
    for that reason.
    """
    names = set(_METRIC.findall(expr))
    if not names:
        return False
    return all(query(f"count({name})") for name in names)


def query(expr: str) -> list[dict] | None:
    url = f"{PROMETHEUS}?{urllib.parse.urlencode({'query': expr})}"
    try:
        with urllib.request.urlopen(url, timeout=20) as response:
            body = json.load(response)
    except (urllib.error.URLError, TimeoutError, ValueError):
        return None
    return list(body.get("data", {}).get("result") or [])


def main() -> int:
    if not DASHBOARDS.is_dir():
        print(f"  no dashboards under {DASHBOARDS}")
        return 1

    drawing = 0
    quiet: list[str] = []
    idle: list[str] = []
    empty: list[str] = []
    unreachable = False

    for path in sorted(DASHBOARDS.glob("*.json")):
        board = json.loads(path.read_text())
        for panel in board.get("panels", []):
            if (panel.get("datasource") or {}).get("type") != "prometheus":
                continue
            for target in panel.get("targets", []):
                expr = target.get("expr")
                if not expr or "$" in expr:
                    # A templated query needs a variable this script has no
                    # business choosing.
                    continue
                label = f"{board['title']} / {panel['title']}"
                result = query(expr)
                if result is None:
                    unreachable = True
                    empty.append(f"{label} (Prometheus unreachable)")
                elif result:
                    drawing += 1
                elif panel["title"] in QUIET_WHEN_WELL:
                    quiet.append(label)
                elif series_exist(expr):
                    idle.append(label)
                else:
                    empty.append(label)

    print(f"\n  {drawing} panels draw data")
    for label in quiet:
        print(f"  quiet {label} — an error panel with nothing in it is the platform working")
    for label in idle:
        print(f"  idle  {label} — the series exists; nothing happened inside the panel's window")
    for label in empty:
        print(f"  EMPTY {label}")

    ok = not empty and not unreachable
    print(f"\n  dashboards: {'PASS' if ok else 'FAIL'}\n")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
