"""PrometheusClient against a real HTTP server standing in for Prometheus."""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

import pytest

from src.prometheus import PrometheusClient, PrometheusError


@pytest.fixture
def prometheus():
    """A server whose next answer is set by the test; records the queries it receives"""

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            server.requests.append((urlparse(self.path).path, parse_qs(urlparse(self.path).query)))
            code, body = server.answer
            payload = body if isinstance(body, bytes) else json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    server.requests = []
    server.answer = (200, {})
    threading.Thread(target=server.serve_forever, args=(0.01,), daemon=True).start()
    server.url = f"http://127.0.0.1:{server.server_port}"
    yield server
    server.shutdown()
    server.server_close()


def vector(*values):
    return {"status": "success", "data": {"resultType": "vector", "result": [
        {"metric": {}, "value": [1790000000.0, str(value)]} for value in values
    ]}}


def test_a_single_series_gives_its_value(prometheus):
    prometheus.answer = (200, vector(0.25))

    assert PrometheusClient(prometheus.url).query("up") == 0.25


def test_the_query_goes_to_the_instant_query_endpoint_url_encoded(prometheus):
    prometheus.answer = (200, vector(1))

    PrometheusClient(prometheus.url + "/").query('sum(rate(x{a="b"}[1m])) / 2')

    assert prometheus.requests == [("/api/v1/query", {"query": ['sum(rate(x{a="b"}[1m])) / 2']})]


def test_a_scalar_gives_its_value(prometheus):
    prometheus.answer = (200, {"status": "success", "data": {"resultType": "scalar", "result": [1790000000.0, "3"]}})

    assert PrometheusClient(prometheus.url).query("1+2") == 3.0


def test_an_empty_vector_is_no_data(prometheus):
    prometheus.answer = (200, vector())

    assert PrometheusClient(prometheus.url).query("up") is None


@pytest.mark.parametrize("raw,expected", [("NaN", None), ("+Inf", float("inf")), ("-Inf", float("-inf")), ("0", 0.0)])
def test_special_values(prometheus, raw, expected):
    prometheus.answer = (200, vector(raw))

    assert PrometheusClient(prometheus.url).query("up") == expected


def test_more_than_one_series_is_an_error(prometheus):
    prometheus.answer = (200, vector(1, 2))

    with pytest.raises(PrometheusError, match="2 series"):
        PrometheusClient(prometheus.url).query("up")


@pytest.mark.parametrize("data", [
    {"resultType": "matrix", "result": []},
    {"resultType": "vector", "result": [{"value": [1, "not-a-number"]}]},
    {"resultType": "vector", "result": [{"nothing": 1}]},
    {"resultType": "scalar", "result": []},
])
def test_an_unusable_result_is_an_error(prometheus, data):
    prometheus.answer = (200, {"status": "success", "data": data})

    with pytest.raises(PrometheusError):
        PrometheusClient(prometheus.url).query("up")


def test_a_rejected_query_reports_prometheus_reason(prometheus):
    prometheus.answer = (400, {"status": "error", "errorType": "bad_data", "error": "parse error at char 3"})

    with pytest.raises(PrometheusError, match="HTTP 400.*parse error"):
        PrometheusClient(prometheus.url).query("sum(")


def test_an_error_status_in_a_200_answer_is_an_error(prometheus):
    prometheus.answer = (200, {"status": "error", "error": "query timed out"})

    with pytest.raises(PrometheusError, match="query timed out"):
        PrometheusClient(prometheus.url).query("up")


@pytest.mark.parametrize("body", [b"<html>oops</html>", b"[1, 2]", b"null"])
def test_an_answer_that_is_not_a_prometheus_response_is_an_error(prometheus, body):
    prometheus.answer = (200, body)

    with pytest.raises(PrometheusError):
        PrometheusClient(prometheus.url).query("up")


def test_a_server_error_is_an_error(prometheus):
    prometheus.answer = (503, b"overloaded")

    with pytest.raises(PrometheusError, match="HTTP 503: overloaded"):
        PrometheusClient(prometheus.url).query("up")


def test_an_unreachable_server_is_an_error():
    with pytest.raises(PrometheusError, match="cannot reach Prometheus"):
        PrometheusClient("http://127.0.0.1:9", timeout=1).query("up")


def test_a_slow_server_times_out(prometheus):
    import time
    from http.server import BaseHTTPRequestHandler

    original = prometheus.RequestHandlerClass.do_GET

    def slow(self):
        time.sleep(1)
        original(self)

    prometheus.RequestHandlerClass.do_GET = slow
    try:
        with pytest.raises(PrometheusError, match="cannot reach Prometheus"):
            PrometheusClient(prometheus.url, timeout=0.2).query("up")
    finally:
        prometheus.RequestHandlerClass.do_GET = original
