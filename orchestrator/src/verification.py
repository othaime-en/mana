"""Evaluate post-deploy checks against Prometheus.

The functions are pure apart from the query they send through the client they
are given, so the rules (what breaches, what counts as a streak, what fails a
deployment) are tested without a server.
"""

import re
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional

from src.policy import Check
from src.prometheus import PrometheusClient, PrometheusError

SAFE_VALUE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")

OK = "ok"
BREACH = "breach"
MISSING = "missing"
ERROR = "error"


@dataclass(frozen=True)
class CheckResult:
    """What one check found this time"""
    name: str
    result: str
    value: Optional[float]
    detail: str
    counts_as_breach: bool


def render_query(query: str, namespace: str, app: str) -> str:
    """
    Substitute {{namespace}} and {{app}} into a query

    The values come from a webhook, so one that could break out of a PromQL
    label matcher (a quote, a brace, a backslash) is refused instead of escaped.
    """
    for placeholder, value in (("{{namespace}}", namespace), ("{{app}}", app)):
        if placeholder in query and not SAFE_VALUE.fullmatch(value):
            raise ValueError(f"{value!r} is not safe to put in a query")
    return query.replace("{{namespace}}", namespace).replace("{{app}}", app)


def evaluate_checks(
    checks: Iterable[Check],
    client: PrometheusClient,
    namespace: str,
    app: str,
    on_missing: str
) -> List[CheckResult]:
    """Run every check once; one failing query never hides the others"""
    results = []
    for check in checks:
        value: Optional[float] = None
        try:
            value = client.query(render_query(check.query, namespace, app))
        except (ValueError, PrometheusError) as error:
            result, detail = ERROR, str(error)
        else:
            if value is None:
                result, detail = MISSING, "the query returned no data"
            elif check.breached_by(value):
                limit, sign = (check.max, ">") if check.max is not None else (check.min, "<")
                result, detail = BREACH, f"{value:g} {sign} {limit:g}"
            else:
                result, detail = OK, f"{value:g}"
        counts = result == BREACH or (result in (MISSING, ERROR) and on_missing == "breach")
        results.append(CheckResult(check.name, result, value, detail, counts))
    return results


def update_streaks(streaks: Dict[str, int], results: Iterable[CheckResult]) -> Dict[str, int]:
    """
    Consecutive breaches per check

    A breach extends the streak and a healthy value resets it. Missing data and
    query errors say nothing about the deployment, so they leave the streak
    alone (unless on_missing made them count as breaches).
    """
    updated = dict(streaks)
    for item in results:
        if item.counts_as_breach:
            updated[item.name] = updated.get(item.name, 0) + 1
        elif item.result == OK:
            updated[item.name] = 0
    return updated


def failed_checks(streaks: Dict[str, int], threshold: int) -> List[str]:
    """The checks whose breaches in a row reached the threshold"""
    return sorted(name for name, streak in streaks.items() if streak >= threshold)
