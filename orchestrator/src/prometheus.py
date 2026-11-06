"""A minimal client for the Prometheus HTTP API: one instant query, one number.

Post-deploy verification only needs to ask "what is this value right now", so
the client returns a float, or None when the query has no data, and raises
PrometheusError for anything that stops it getting an answer.
"""

import math
from typing import Optional

import requests


class PrometheusError(Exception):
    """Prometheus cannot be reached, rejected the query, or answered with something unusable"""


class PrometheusClient:
    """Runs instant queries against one Prometheus server"""

    def __init__(self, base_url: str, timeout: float = 5.0):
        self._url = f"{base_url.rstrip('/')}/api/v1/query"
        self._timeout = timeout

    def query(self, expression: str) -> Optional[float]:
        """
        The value of a query that returns one series or a scalar

        None means no data (an empty result, or NaN such as a quantile over no
        traffic). More than one series is an error: the query must aggregate.
        """
        try:
            response = requests.get(self._url, params={"query": expression}, timeout=self._timeout)
        except requests.RequestException as error:
            raise PrometheusError(f"cannot reach Prometheus: {type(error).__name__}") from error
        if response.status_code != 200:
            raise PrometheusError(f"Prometheus answered HTTP {response.status_code}: {response.text[:200]}")
        try:
            body = response.json()
        except ValueError as error:
            raise PrometheusError("Prometheus did not answer with JSON") from error
        if not isinstance(body, dict) or body.get("status") != "success":
            detail = body.get("error") if isinstance(body, dict) else None
            raise PrometheusError(f"the query failed: {detail or 'no status'}")
        return self._number(body.get("data") or {})

    @staticmethod
    def _number(data: dict) -> Optional[float]:
        kind, result = data.get("resultType"), data.get("result")
        try:
            if kind == "scalar":
                raw = result[1]
            elif kind == "vector":
                if not result:
                    return None
                if len(result) > 1:
                    raise PrometheusError(f"the query returned {len(result)} series; aggregate it to one")
                raw = result[0]["value"][1]
            else:
                raise PrometheusError(f"unsupported result type: {kind}")
            number = float(raw)
        except (TypeError, ValueError, KeyError, IndexError) as error:
            raise PrometheusError("the result is not a number") from error
        return None if math.isnan(number) else number
