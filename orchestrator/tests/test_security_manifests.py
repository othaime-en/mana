"""Static checks on the security manifests and their Terraform mirror."""

import os
import re
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
SECURITY_DIR = ROOT / "kubernetes" / "security"
SCRIPT = ROOT / "scripts" / "test-network-policies.sh"

pytestmark = pytest.mark.skipif(
    not SECURITY_DIR.is_dir(), reason="security manifests are not available in this checkout"
)


def documents(path: Path) -> list[dict]:
    return [doc for doc in yaml.safe_load_all(path.read_text()) if doc]


def policies() -> dict[tuple[str, str], dict]:
    docs = documents(SECURITY_DIR / "network-policies.yaml")
    assert all(doc["kind"] == "NetworkPolicy" for doc in docs)
    return {(doc["metadata"]["namespace"], doc["metadata"]["name"]): doc["spec"] for doc in docs}


def peers(rules: list[dict], direction: str) -> list[dict]:
    return [peer for rule in rules for peer in rule.get(direction, [])]


def test_security_directory_holds_only_current_kinds():
    obsolete = {"PodSecurityPolicy", "SecurityContextConstraints", "Secret", "ClusterRole", "ClusterRoleBinding"}
    kinds = {
        doc["kind"]
        for path in SECURITY_DIR.glob("*.yaml")
        for doc in documents(path)
    }
    assert kinds & obsolete == set()


@pytest.mark.parametrize("namespace", ["production", "staging", "orchestrator"])
def test_every_workload_namespace_starts_from_default_deny(namespace):
    spec = policies()[(namespace, "default-deny-all")]
    assert spec["podSelector"] == {}
    assert set(spec["policyTypes"]) == {"Ingress", "Egress"}
    assert "ingress" not in spec and "egress" not in spec


def test_no_policy_selects_every_namespace_or_uses_the_hand_made_name_label():
    text = (SECURITY_DIR / "network-policies.yaml").read_text()
    assert "namespaceSelector: {}" not in text
    for spec in policies().values():
        for rule in spec.get("ingress", []) + spec.get("egress", []):
            for peer in rule.get("from", []) + rule.get("to", []):
                selector = peer.get("namespaceSelector")
                if selector is not None:
                    assert selector != {}
                    assert "name" not in selector.get("matchLabels", {})


def namespace_names(rules: list[dict], direction: str) -> set[str]:
    names = set()
    for peer in peers(rules, direction):
        selector = peer.get("namespaceSelector", {})
        names |= set(selector.get("matchLabels", {}).values())
    return names


def test_orchestrator_accepts_traffic_only_from_monitoring_and_the_runner_namespace():
    spec = policies()[("orchestrator", "orchestrator-netpol")]
    assert namespace_names(spec["ingress"], "from") == {"monitoring", "ci-runners"}
    assert all(
        port["port"] == 8000 for rule in spec["ingress"] for port in rule["ports"]
    )


def test_orchestrator_can_reach_dns_redis_pods_and_the_api_server():
    spec = policies()[("orchestrator", "orchestrator-netpol")]
    ports = {port["port"] for rule in spec["egress"] for port in rule["ports"]}
    assert {53, 6379, 5000, 443, 6443} <= ports
    probes = next(
        rule for rule in spec["egress"] if any(p["port"] == 5000 for p in rule["ports"])
    )
    selector = probes["to"][0]
    assert selector["podSelector"]["matchLabels"] == {"app": "sample-app"}
    assert set(selector["namespaceSelector"]["matchExpressions"][0]["values"]) == {
        "production", "staging", "canary",
    }


def test_redis_accepts_only_orchestrator_pods_and_sends_nothing():
    spec = policies()[("orchestrator", "redis-netpol")]
    assert [p["podSelector"]["matchLabels"] for p in peers(spec["ingress"], "from")] == [
        {"app": "orchestrator"}
    ]
    assert "egress" not in spec and "Egress" in spec["policyTypes"]


@pytest.mark.parametrize("namespace", ["production", "staging"])
def test_sample_app_accepts_the_orchestrator_and_only_resolves_dns(namespace):
    spec = policies()[(namespace, "sample-app-netpol")]
    assert "orchestrator" in namespace_names(spec["ingress"], "from")
    assert {port["port"] for rule in spec["egress"] for port in rule["ports"]} == {53}


def test_terraform_mirror_selects_namespaces_by_metadata_name_only():
    text = (ROOT / "terraform" / "modules" / "security" / "main.tf").read_text()
    assert not re.search(r"match_labels\s*=\s*\{\s*name\s*=", text)
    assert "namespace_selector {}" not in text
    assert text.count("kubernetes.io/metadata.name") >= 10
    for name in ("default_deny_production", "default_deny_staging", "default_deny_orchestrator", "redis"):
        assert f'resource "kubernetes_network_policy" "{name}"' in text


def test_enforcement_script_refuses_a_non_kind_context(tmp_path):
    stub = tmp_path / "kubectl"
    stub.write_text('#!/usr/bin/env bash\n[[ "$1 $2" == "config current-context" ]] && echo prod-eu\necho "$*" >> "$STUB_LOG"\n')
    stub.chmod(0o755)
    env = dict(os.environ, PATH=f"{tmp_path}:{os.environ['PATH']}", STUB_LOG=str(tmp_path / "log"))
    result = subprocess.run([str(SCRIPT)], env=env, capture_output=True, text=True)
    assert result.returncode == 1
    assert "not a kind cluster" in result.stderr
    calls = (tmp_path / "log").read_text().splitlines()
    assert calls == ["config current-context"]


RBAC_FILE = ROOT / "orchestrator" / "k8s" / "rbac.yaml"
SRC = ROOT / "orchestrator" / "src"

API_CALLS = {
    "read_namespaced_deployment": ("apps", "deployments", "get"),
    "patch_namespaced_deployment": ("apps", "deployments", "patch"),
    "list_namespaced_replica_set": ("apps", "replicasets", "list"),
    "list_namespaced_pod": ("", "pods", "list"),
}
UNSCOPED_CALLS = {"get_code"}


def calls_in_source() -> set[str]:
    found: set[str] = set()
    for path in SRC.rglob("*.py"):
        found |= set(re.findall(r"\.k8s_(?:apps|core|version)\.(\w+)\(", path.read_text()))
    return found


def test_every_kubernetes_call_is_known_to_this_test():
    assert calls_in_source() - UNSCOPED_CALLS <= set(API_CALLS), (
        "a new Kubernetes call needs a matching RBAC rule and an entry in API_CALLS"
    )


def role_docs() -> list[dict]:
    return documents(RBAC_FILE)


def test_orchestrator_has_no_cluster_wide_rbac():
    for path in (ROOT / "orchestrator" / "k8s").glob("*.yaml"):
        kinds = {doc["kind"] for doc in documents(path)}
        assert not kinds & {"ClusterRole", "ClusterRoleBinding"}, path.name
    text = (ROOT / "terraform" / "modules" / "orchestrator" / "main.tf").read_text()
    assert "kubernetes_cluster_role" not in text


@pytest.mark.parametrize("namespace", ["production", "staging", "canary"])
def test_role_grants_exactly_what_the_code_calls(namespace):
    docs = [doc for doc in role_docs() if doc["metadata"]["namespace"] == namespace]
    role = next(doc for doc in docs if doc["kind"] == "Role")
    granted = {
        (group, resource, verb)
        for rule in role["rules"]
        for group in rule["apiGroups"]
        for resource in rule["resources"]
        for verb in rule["verbs"]
    }
    required = {API_CALLS[call] for call in calls_in_source() - UNSCOPED_CALLS}
    assert granted == required
    binding = next(doc for doc in docs if doc["kind"] == "RoleBinding")
    assert binding["subjects"] == [
        {"kind": "ServiceAccount", "name": "orchestrator-sa", "namespace": "orchestrator"}
    ]
    assert binding["roleRef"]["name"] == role["metadata"]["name"]


def test_terraform_roles_match_the_manifest_rules():
    text = (ROOT / "terraform" / "modules" / "orchestrator" / "main.tf").read_text()
    for verbs in ('["get", "patch"]', '["list"]'):
        assert verbs in text
    for forbidden in ('"update"', '"watch"', '"*"', '"create"', '"delete"'):
        assert forbidden not in text.split('resource "kubernetes_role" "orchestrator"')[1].split("resource \"kubernetes_config_map\"")[0]


WORKLOADS = {
    "orchestrator": ROOT / "orchestrator" / "k8s" / "deployment.yaml",
    "redis": ROOT / "orchestrator" / "k8s" / "redis.yaml",
    "sample-app": ROOT / "sample-app" / "k8s" / "deployment.yaml",
}
ALLOWED_VOLUME_TYPES = {
    "emptyDir", "secret", "configMap", "projected", "downwardAPI", "persistentVolumeClaim", "ephemeral",
}


def pod_spec(name: str) -> dict:
    deployment = next(
        doc for doc in documents(WORKLOADS[name])
        if doc["kind"] == "Deployment" and doc["metadata"]["name"] == name
    )
    return deployment["spec"]["template"]["spec"]


@pytest.mark.parametrize("name", WORKLOADS)
def test_workload_satisfies_the_restricted_pod_security_standard(name):
    spec = pod_spec(name)
    pod_context = spec.get("securityContext", {})
    assert pod_context.get("seccompProfile", {}).get("type") == "RuntimeDefault"
    for container in spec["containers"] + spec.get("initContainers", []):
        context = container["securityContext"]
        assert context["allowPrivilegeEscalation"] is False
        assert context["readOnlyRootFilesystem"] is True
        assert context["capabilities"]["drop"] == ["ALL"]
        assert pod_context.get("runAsNonRoot") or context.get("runAsNonRoot")
        assert not context.get("privileged")
        assert container["resources"]["limits"] and container["resources"]["requests"]
        assert container.get("livenessProbe") or container.get("readinessProbe")
    for volume in spec.get("volumes", []):
        assert set(volume) - {"name"} <= ALLOWED_VOLUME_TYPES
    for flag in ("hostNetwork", "hostPID", "hostIPC"):
        assert not spec.get(flag)


@pytest.mark.parametrize("name", ["orchestrator", "sample-app"])
def test_python_workloads_get_a_writable_tmp(name):
    spec = pod_spec(name)
    mounts = {m["mountPath"]: m["name"] for c in spec["containers"] for m in c.get("volumeMounts", [])}
    volume = next(v for v in spec["volumes"] if v["name"] == mounts["/tmp"])
    assert "emptyDir" in volume


@pytest.mark.parametrize("name,expected", [("sample-app", False), ("redis", False)])
def test_workloads_that_do_not_call_the_api_server_get_no_token(name, expected):
    assert pod_spec(name)["automountServiceAccountToken"] is expected


def test_orchestrator_keeps_its_service_account_token():
    spec = pod_spec("orchestrator")
    assert spec["serviceAccountName"] == "orchestrator-sa"
    assert spec.get("automountServiceAccountToken", True) is True


def test_redis_runs_as_its_own_non_root_user():
    context = pod_spec("redis")["securityContext"]
    assert context["runAsUser"] == context["runAsGroup"] == context["fsGroup"] == 999


def test_namespaces_enforce_the_restricted_standard():
    labelled = {}
    for path in (SECURITY_DIR / "namespaces.yaml", WORKLOADS["orchestrator"]):
        for doc in documents(path):
            if doc["kind"] == "Namespace":
                labelled[doc["metadata"]["name"]] = doc["metadata"].get("labels", {})
    assert set(labelled) == {"production", "staging", "canary", "orchestrator"}
    for labels in labelled.values():
        assert labels["pod-security.kubernetes.io/enforce"] == "restricted"


def test_terraform_mirrors_the_pod_hardening():
    modules = ROOT / "terraform" / "modules"
    for module in ("orchestrator", "sample-app"):
        text = (modules / module / "main.tf").read_text()
        assert 'type = "RuntimeDefault"' in text
        assert 'mount_path = "/tmp"' in text
        assert "read_only_root_filesystem  = true" in text
    assert "automount_service_account_token = false" in (modules / "sample-app" / "main.tf").read_text()
    namespaces = (modules / "namespaces" / "main.tf").read_text()
    assert namespaces.count("local.restricted") == 4


def test_ci_deployer_is_namespaced_and_cannot_create_or_delete_workloads():
    docs = documents(SECURITY_DIR / "ci-deployer-rbac.yaml")
    roles = [doc for doc in docs if doc["kind"] == "Role"]
    assert {role["metadata"]["namespace"] for role in roles} == {"production", "staging"}
    assert not [doc for doc in docs if doc["kind"].startswith("Cluster")]
    for role in roles:
        for rule in role["rules"]:
            assert "*" not in rule["verbs"] + rule["resources"] + rule["apiGroups"]
            if "deployments" in rule["resources"]:
                assert not {"create", "delete", "update"} & set(rule["verbs"])
            assert not {"secrets", "configmaps"} & set(rule["resources"])


def test_workflows_do_not_use_a_personal_access_token_for_the_registry():
    text = (ROOT / ".github" / "workflows" / "build-and-test.yml").read_text()
    assert "GHCR_PAT" not in text
    assert "secrets.GITHUB_TOKEN" in text
