"""Fail when an alert, recording rule or dashboard panel uses a metric nothing provides"""

import json
import re
from pathlib import Path
from typing import Iterator, List, Set, Tuple

import pytest
import yaml
from prometheus_client.metrics import MetricWrapperBase
from promql_parser import parse

from src import metrics

REPO_ROOT = Path(__file__).resolve().parents[2]
PROMETHEUS_DIR = REPO_ROOT / "monitoring" / "prometheus"
DASHBOARD_DIR = REPO_ROOT / "monitoring" / "grafana" / "dashboards"

EXTERNAL_METRICS = {
    "up",
    "flask_http_request_total",
    "flask_http_request_duration_seconds_bucket",
    "flask_http_request_duration_seconds_count",
    "flask_http_request_duration_seconds_sum",
    "kube_deployment_spec_replicas",
    "kube_deployment_status_replicas_available",
    "kube_node_info",
    "kube_node_status_allocatable",
    "kube_node_status_capacity",
    "kube_node_status_condition",
    "kube_pod_container_resource_requests",
    "kube_pod_container_status_restarts_total",
    "kube_pod_info",
    "kube_pod_status_phase",
    "kube_service_info",
    "container_cpu_usage_seconds_total",
    "container_memory_working_set_bytes",
    "container_spec_cpu_period",
    "container_spec_cpu_quota",
    "container_spec_memory_limit_bytes",
    "node_cpu_seconds_total",
    "node_filesystem_avail_bytes",
    "node_filesystem_size_bytes",
    "node_memory_MemAvailable_bytes",
    "node_memory_MemTotal_bytes",
    "prometheus_tsdb_retention_limit_bytes",
    "prometheus_tsdb_storage_blocks_bytes",
}

GRAFANA_INTERVAL_VARIABLES = re.compile(r"\$\{?__(rate_interval|interval|range)\}?")


def emitted_metric_names() -> Set[str]:
    """Sample names the orchestrator exposes, derived from src.metrics"""
    names = {"active_deployments"}
    for value in vars(metrics).values():
        if not isinstance(value, MetricWrapperBase):
            continue
        if value._type == "counter":
            names.add(f"{value._name}_total")
        elif value._type == "histogram":
            names.update(f"{value._name}_{suffix}" for suffix in ("bucket", "sum", "count"))
        else:
            names.add(value._name)
    return names


def metric_names(expression: str) -> Set[str]:
    """Every metric name selected by a PromQL expression"""
    found: Set[str] = set()

    def walk(node) -> None:
        if type(node).__name__ == "VectorSelector":
            found.add(node.name or f"<selector without a name: {node.prettify()}>")
            return
        for attribute in ("lhs", "rhs", "expr", "vector_selector", "param"):
            child = getattr(node, attribute, None)
            if child is not None and not isinstance(child, (str, int, float)):
                walk(child)
        for child in getattr(node, "args", None) or []:
            walk(child)

    walk(parse(GRAFANA_INTERVAL_VARIABLES.sub("5m", expression)))
    return found


def rule_files() -> Iterator[Tuple[str, dict]]:
    for filename in ("alerts.yml", "recording-rules.yml"):
        for group in yaml.safe_load((PROMETHEUS_DIR / filename).read_text())["groups"]:
            for rule in group["rules"]:
                yield filename, rule


def recorded_names() -> Set[str]:
    return {rule["record"] for _, rule in rule_files() if "record" in rule}


def rule_cases() -> List[Tuple[str, str, str]]:
    return [
        (filename, rule.get("record") or rule["alert"], rule["expr"])
        for filename, rule in rule_files()
    ]


def panel_cases() -> List[Tuple[str, str, str]]:
    cases = []
    for path in sorted(DASHBOARD_DIR.glob("*.json")):
        for panel in json.loads(path.read_text()).get("panels", []):
            datasource = panel.get("datasource")
            kind = datasource.get("type") if isinstance(datasource, dict) else datasource
            if kind == "loki":
                continue
            for target in panel.get("targets", []):
                if target.get("expr"):
                    cases.append((path.name, panel["title"], target["expr"]))
    return cases


def missing_metrics(expression: str) -> Set[str]:
    known = emitted_metric_names() | EXTERNAL_METRICS | recorded_names()
    return metric_names(expression) - known


class TestMetricNameExtraction:
    @pytest.mark.parametrize("expression,expected", [
        ('up{job="orchestrator"} == 0', {"up"}),
        ('sum(rate(a_total{x="1"}[5m])) / sum(rate(b_total[5m]))', {"a_total", "b_total"}),
        ("histogram_quantile(0.95, sum by (le) (rate(h_bucket[$__rate_interval])))", {"h_bucket"}),
        ("sum(deployment:active_count:namespace) or vector(0)", {"deployment:active_count:namespace"}),
        ("max_over_time(rate(q[1m])[10m:1m])", {"q"}),
        ("-(sum(a)) > bool 3 and on (y) topk(3, z)", {"a", "z"}),
    ])
    def test_finds_every_selector(self, expression, expected):
        assert metric_names(expression) == expected

    def test_a_selector_without_a_name_is_reported(self):
        (name,) = metric_names('{app="orchestrator"}')

        assert name.startswith("<selector without a name")

    def test_an_unknown_metric_is_flagged(self):
        assert missing_metrics("rate(never_emitted_total[5m])") == {"never_emitted_total"}

    def test_orchestrator_names_are_derived_from_the_code(self):
        names = emitted_metric_names()

        assert {"deployments_total", "mana_recovery_duration_seconds_bucket", "active_deployments"} <= names


def test_rule_files_are_not_empty():
    assert len(rule_cases()) > 50
    assert len(panel_cases()) > 30


@pytest.mark.parametrize("source,name,expression", rule_cases(), ids=lambda v: str(v)[:40])
def test_rules_only_use_known_metrics(source, name, expression):
    assert missing_metrics(expression) == set(), f"{source}: {name}"


@pytest.mark.parametrize("source,title,expression", panel_cases(), ids=lambda v: str(v)[:40])
def test_panels_only_use_known_metrics(source, title, expression):
    assert missing_metrics(expression) == set(), f"{source}: {title}"
