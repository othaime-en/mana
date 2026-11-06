import itertools
import os
import time
from unittest.mock import patch
from pathlib import Path

import pytest

from src.config import OrchestratorConfig
from src.diagnosis import Diagnosis
from src.orchestrator import FailureType
from src.policy import (
    DEFAULT_POLICY,
    FAILURE_TYPES,
    NON_ROLLBACK_FAILURES,
    Action,
    Policy,
    PolicyError,
    Reason,
    Rule,
    decide,
    load_policy,
)

EXAMPLE = Path(__file__).resolve().parents[1] / "policy.example.yaml"

RETRY_ONCE = Rule(action="retry", max_attempts=1, then="rollback")
TRANSIENT = Diagnosis("ImagePullBackOff", True)
DETERMINISTIC = Diagnosis("CrashLoopBackOff", False)


def policy_with(**sections) -> dict:
    return {**DEFAULT_POLICY, **sections}


def test_policy_failure_types_are_the_ones_the_orchestrator_knows():
    assert set(FAILURE_TYPES) == {member.value for member in FailureType}


def test_the_default_policy_covers_every_failure_type():
    policy = load_policy()

    assert set(policy.default) == set(FAILURE_TYPES)


def test_the_default_policy_rolls_back_post_deploy_failures_at_once():
    policy = load_policy()

    for failure_type in ("health_check_failure", "smoke_test_failure", "rollout_stuck"):
        assert policy.rule_for("production", "sample-app", failure_type).action is Action.ROLLBACK
    assert policy.rule_for("production", "sample-app", "test_failure").action is Action.ALERT


def test_the_example_file_is_a_valid_policy_with_the_built_in_defaults():
    example = load_policy(str(EXAMPLE))
    built_in = load_policy()

    assert example.default == built_in.default
    assert example.rollback == built_in.rollback
    assert example.circuit_breaker == built_in.circuit_breaker
    assert example.rule_for("staging", "sample-app", "health_check_failure").action is Action.RETRY
    assert example.rule_for("production", "sample-app", "health_check_failure").action is Action.ROLLBACK


class TestRuleShape:
    def test_a_retry_rule_needs_max_attempts(self):
        with pytest.raises(ValueError, match="needs max_attempts"):
            Rule(action="retry")

    @pytest.mark.parametrize("attempts", [0, -1])
    def test_max_attempts_must_be_positive(self, attempts):
        with pytest.raises(ValueError):
            Rule(action="retry", max_attempts=attempts)

    def test_then_cannot_be_retry(self):
        with pytest.raises(ValueError, match="rollback or alert"):
            Rule(action="retry", max_attempts=1, then="retry")

    @pytest.mark.parametrize("fields", [
        {"action": "alert", "max_attempts": 1},
        {"action": "rollback", "then": "alert"},
    ])
    def test_retry_settings_do_not_apply_to_other_actions(self, fields):
        with pytest.raises(ValueError, match="only apply to retry rules"):
            Rule(**fields)

    def test_unknown_keys_are_rejected(self):
        with pytest.raises(ValueError):
            Rule(action="alert", backoff="fast")

    def test_then_defaults_to_alert(self):
        assert Rule(action="retry", max_attempts=2).then is Action.ALERT

    @pytest.mark.parametrize("rule,ceiling,expected", [
        (Rule(action="retry", max_attempts=5, then="rollback"), 3, 3),
        (Rule(action="retry", max_attempts=2), 3, 2),
        (Rule(action="alert"), 1, None),
    ])
    def test_max_retries_is_a_ceiling(self, rule, ceiling, expected):
        assert rule.capped(ceiling).max_attempts == expected

    def test_capping_keeps_the_follow_up_action(self):
        assert RETRY_ONCE.capped(1).then is Action.ROLLBACK


class TestPolicyValidation:
    def test_a_type_missing_from_default_uses_the_built_in_rule(self):
        incomplete = {key: value for key, value in DEFAULT_POLICY["default"].items() if key != "rollout_stuck"}

        policy = Policy.model_validate(policy_with(default=incomplete))

        assert policy.rule_for("production", "app", "rollout_stuck") == load_policy().default["rollout_stuck"]

    def test_a_policy_with_only_overrides_still_has_every_rule(self):
        policy = Policy.model_validate({"overrides": [
            {"match": {"namespace": "staging"}, "rules": {"timeout": {"action": "alert"}}}
        ]})

        assert set(policy.default) == set(FAILURE_TYPES)
        assert policy.rule_for("production", "app", "timeout").action is Action.RETRY
        assert policy.rule_for("staging", "app", "timeout").action is Action.ALERT

    def test_a_rule_in_default_replaces_the_built_in_one(self):
        policy = Policy.model_validate({"default": {"timeout": {"action": "alert"}}})

        assert policy.rule_for("production", "app", "timeout").action is Action.ALERT
        assert policy.rule_for("production", "app", "health_check_failure").action is Action.ROLLBACK

    def test_a_default_that_is_not_a_mapping_is_rejected(self):
        with pytest.raises(ValueError):
            Policy.model_validate({"default": ["timeout"]})

    def test_unknown_failure_types_are_rejected(self):
        extra = {**DEFAULT_POLICY["default"], "disk_full": {"action": "alert"}}

        with pytest.raises(ValueError, match="unknown failure type"):
            Policy.model_validate(policy_with(default=extra))

    @pytest.mark.parametrize("failure_type", sorted(NON_ROLLBACK_FAILURES))
    @pytest.mark.parametrize("rule", [
        {"action": "rollback"},
        {"action": "retry", "max_attempts": 1, "then": "rollback"},
    ])
    def test_failures_before_deployment_cannot_roll_back(self, failure_type, rule):
        default = {**DEFAULT_POLICY["default"], failure_type: rule}

        with pytest.raises(ValueError, match="cannot roll back"):
            Policy.model_validate(policy_with(default=default))

    def test_the_same_guard_applies_to_overrides(self):
        override = {"match": {"namespace": "staging"}, "rules": {"test_failure": {"action": "rollback"}}}

        with pytest.raises(ValueError, match="cannot roll back"):
            Policy.model_validate(policy_with(overrides=[override]))

    def test_an_override_needs_a_rule(self):
        with pytest.raises(ValueError, match="at least one rule"):
            Policy.model_validate(policy_with(overrides=[{"match": {}, "rules": {}}]))

    def test_an_override_cannot_name_an_unknown_failure_type(self):
        override = {"match": {}, "rules": {"nope": {"action": "alert"}}}

        with pytest.raises(ValueError, match="unknown failure type"):
            Policy.model_validate(policy_with(overrides=[override]))

    def test_the_version_must_be_supported(self):
        with pytest.raises(ValueError, match="unsupported policy version"):
            Policy.model_validate(policy_with(version=2))

    def test_unknown_top_level_keys_are_rejected(self):
        with pytest.raises(ValueError):
            Policy.model_validate(policy_with(notify=["slack"]))

    @pytest.mark.parametrize("section", [
        {"rollback": {"verify_timeout_seconds": 5}},
        {"rollback": {"deny_ttl_seconds": 1}},
        {"circuit_breaker": {"max_rollbacks": 0}},
        {"circuit_breaker": {"window_seconds": 10}},
    ])
    def test_settings_are_bounded(self, section):
        with pytest.raises(ValueError):
            Policy.model_validate(policy_with(**section))


class TestRuleResolution:
    @pytest.fixture
    def policy(self):
        return Policy.model_validate(policy_with(overrides=[
            {"match": {"namespace": "staging", "app": "api"},
             "rules": {"health_check_failure": {"action": "alert"}}},
            {"match": {"namespace": "staging"},
             "rules": {"health_check_failure": {"action": "retry", "max_attempts": 2, "then": "rollback"}}},
            {"match": {"app": "batch"}, "rules": {"timeout": {"action": "alert"}}},
        ]))

    def test_the_first_matching_override_wins(self, policy):
        assert policy.rule_for("staging", "api", "health_check_failure").action is Action.ALERT
        assert policy.rule_for("staging", "web", "health_check_failure").action is Action.RETRY

    def test_an_override_only_replaces_the_types_it_lists(self, policy):
        assert policy.rule_for("staging", "api", "smoke_test_failure").action is Action.ROLLBACK

    def test_a_match_on_one_field_ignores_the_other(self, policy):
        assert policy.rule_for("production", "batch", "timeout").action is Action.ALERT
        assert policy.rule_for("production", "web", "timeout").action is Action.RETRY

    def test_other_namespaces_use_the_default(self, policy):
        assert policy.rule_for("production", "api", "health_check_failure").action is Action.ROLLBACK


def test_an_override_written_with_the_yaml_1_1_boolean_key_is_rejected(tmp_path):
    path = tmp_path / "policy.yaml"
    path.write_text(
        EXAMPLE.read_text().replace("    rules:\n", "    on:\n")
    )

    with pytest.raises(PolicyError, match="overrides.0"):
        load_policy(str(path))


class TestLoadPolicy:
    def test_a_yaml_file_is_loaded(self, tmp_path):
        path = tmp_path / "policy.yaml"
        path.write_text("default:\n  build_failure: {action: alert}\n  test_failure: {action: alert}\n"
                        "  deployment_failure: {action: rollback}\n  health_check_failure: {action: rollback}\n"
                        "  smoke_test_failure: {action: rollback}\n  timeout: {action: alert}\n")

        policy = load_policy(str(path))

        assert policy.rule_for("production", "app", "deployment_failure").action is Action.ROLLBACK
        assert policy.rollback.verify_timeout_seconds == 120

    def test_json_is_accepted_too(self, tmp_path):
        import json

        path = tmp_path / "policy.json"
        path.write_text(json.dumps(DEFAULT_POLICY))

        assert load_policy(str(path)).default == load_policy().default

    def test_a_missing_file_is_reported(self, tmp_path):
        with pytest.raises(PolicyError, match="cannot read policy file"):
            load_policy(str(tmp_path / "absent.yaml"))

    def test_broken_yaml_is_reported(self, tmp_path):
        path = tmp_path / "policy.yaml"
        path.write_text("default: [unclosed")

        with pytest.raises(PolicyError, match="not valid YAML"):
            load_policy(str(path))

    @pytest.mark.parametrize("content", ["", "- a\n- b\n", "just text"])
    def test_a_file_that_is_not_a_mapping_is_reported(self, tmp_path, content):
        path = tmp_path / "policy.yaml"
        path.write_text(content)

        with pytest.raises(PolicyError, match="must contain a mapping"):
            load_policy(str(path))

    def test_validation_errors_name_the_field_and_the_file(self, tmp_path):
        path = tmp_path / "policy.yaml"
        path.write_text("default:\n  timeout: {action: retry}\n")

        with pytest.raises(PolicyError) as error:
            load_policy(str(path))

        assert str(path) in str(error.value)
        assert "default.timeout" in str(error.value)


def test_policy_file_is_read_from_the_environment(monkeypatch, tmp_path):
    path = tmp_path / "policy.yaml"
    path.write_text("x: 1")
    monkeypatch.setenv("POLICY_FILE", str(path))

    assert OrchestratorConfig.from_env().policy_file == str(path)


def test_a_missing_policy_file_stops_startup(monkeypatch, tmp_path):
    monkeypatch.setenv("POLICY_FILE", str(tmp_path / "absent.yaml"))

    with pytest.raises(ValueError, match="POLICY_FILE not found"):
        OrchestratorConfig.from_env().validate()


class TestDecide:
    def test_an_alert_rule_alerts(self):
        decision = decide(Rule(action="alert"), 1, can_retry=True, has_rollback_target=True)

        assert (decision.action, decision.reason) == (Action.ALERT, Reason.POLICY)

    def test_a_rollback_rule_rolls_back_on_the_first_failure(self):
        decision = decide(Rule(action="rollback"), 1, can_retry=True, has_rollback_target=True)

        assert (decision.action, decision.reason) == (Action.ROLLBACK, Reason.POLICY)

    def test_a_rollback_without_a_target_becomes_an_alert(self):
        decision = decide(Rule(action="rollback"), 1, can_retry=True, has_rollback_target=False)

        assert (decision.action, decision.reason) == (Action.ALERT, Reason.NO_ROLLBACK_TARGET)

    @pytest.mark.parametrize("attempt,expected", [(1, Action.RETRY), (2, Action.ROLLBACK)])
    def test_retries_run_out(self, attempt, expected):
        decision = decide(RETRY_ONCE, attempt, can_retry=True, has_rollback_target=True)

        assert decision.action is expected
        if expected is Action.ROLLBACK:
            assert decision.reason is Reason.RETRIES_EXHAUSTED

    def test_without_a_workflow_run_the_rule_moves_on_to_then(self):
        decision = decide(RETRY_ONCE, 1, can_retry=False, has_rollback_target=True)

        assert (decision.action, decision.reason) == (Action.ROLLBACK, Reason.RETRY_UNAVAILABLE)

    def test_a_deterministic_cause_skips_the_retries(self):
        decision = decide(RETRY_ONCE, 1, can_retry=True, has_rollback_target=True, diagnosis=DETERMINISTIC)

        assert (decision.action, decision.reason) == (Action.ROLLBACK, Reason.DETERMINISTIC)
        assert "CrashLoopBackOff" in decision.detail

    def test_a_transient_cause_keeps_the_retries(self):
        decision = decide(RETRY_ONCE, 1, can_retry=True, has_rollback_target=True, diagnosis=TRANSIENT)

        assert decision.action is Action.RETRY

    def test_a_deterministic_cause_on_a_rule_that_alerts_after_retrying_alerts(self):
        rule = Rule(action="retry", max_attempts=2)

        decision = decide(rule, 1, can_retry=True, has_rollback_target=True, diagnosis=DETERMINISTIC)

        assert (decision.action, decision.reason) == (Action.ALERT, Reason.DETERMINISTIC)

    def test_a_diagnosis_never_changes_a_rule_that_does_not_retry(self):
        decision = decide(Rule(action="alert"), 1, can_retry=True, has_rollback_target=True, diagnosis=DETERMINISTIC)

        assert (decision.action, decision.reason) == (Action.ALERT, Reason.POLICY)

    def test_the_missing_target_check_applies_after_falling_through(self):
        decision = decide(RETRY_ONCE, 2, can_retry=True, has_rollback_target=False)

        assert (decision.action, decision.reason) == (Action.ALERT, Reason.NO_ROLLBACK_TARGET)


RULES = [
    Rule(action="alert"),
    Rule(action="rollback"),
    Rule(action="retry", max_attempts=1),
    Rule(action="retry", max_attempts=3, then="rollback"),
    Rule(action="retry", max_attempts=3, then="alert"),
]


@pytest.mark.parametrize(
    "rule,attempt,can_retry,has_target,diagnosis",
    itertools.product(RULES, range(1, 6), (True, False), (True, False), (None, TRANSIENT, DETERMINISTIC)),
)
def test_invariants_hold_for_every_combination(rule, attempt, can_retry, has_target, diagnosis):
    decision = decide(rule, attempt, can_retry=can_retry, has_rollback_target=has_target, diagnosis=diagnosis)

    if decision.action is Action.RETRY:
        assert rule.action is Action.RETRY
        assert attempt <= rule.max_attempts
        assert can_retry
        assert diagnosis is None or diagnosis.transient
    if decision.action is Action.ROLLBACK:
        assert has_target
        assert rule.rolls_back()
    if rule.action is not Action.RETRY:
        assert decision.action is (Action.ROLLBACK if rule.action is Action.ROLLBACK and has_target else Action.ALERT)
    if rule.action is Action.RETRY and attempt > rule.max_attempts:
        assert decision.action is not Action.RETRY


class TestPolicySource:
    """The policy in force follows its file, and a bad edit never replaces a good policy"""

    ALERT_ON_HEALTH = {**DEFAULT_POLICY["default"], "health_check_failure": {"action": "alert"}}

    @pytest.fixture
    def clock(self):
        class Clock:
            now = 1000.0

            def __call__(self):
                return self.now

            def advance(self, seconds=6.0):
                self.now += seconds

        return Clock()

    def write(self, path, default=None, version=1):
        import json

        path.write_text(json.dumps({"version": version, "default": default or DEFAULT_POLICY["default"]}))
        stat = path.stat()
        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))

    def source(self, tmp_path, clock, events, interval=5.0):
        from src.policy import PolicySource

        path = tmp_path / "policy.yaml"
        self.write(path)
        return path, PolicySource(str(path), interval, events.append, clock=clock)

    def health_action(self, source):
        return source.policy.rule_for("production", "app", "health_check_failure").action

    def test_without_a_file_it_serves_the_built_in_policy(self):
        from src.policy import PolicySource

        source = PolicySource()

        assert source.policy.default == load_policy().default
        assert source.path == ""

    def test_a_given_policy_is_served_as_is_and_never_reloaded(self, tmp_path, clock):
        from src.policy import PolicySource

        fixed = Policy.model_validate(policy_with(default=self.ALERT_ON_HEALTH))
        source = PolicySource(str(tmp_path / "ignored.yaml"), policy=fixed, clock=clock)
        clock.advance()

        assert source.policy is fixed and source.path == ""

    def test_a_changed_file_is_picked_up_after_the_interval(self, tmp_path, clock):
        events = []
        path, source = self.source(tmp_path, clock, events)
        assert self.health_action(source) is Action.ROLLBACK
        before = source.loaded_at

        self.write(path, self.ALERT_ON_HEALTH)
        assert self.health_action(source) is Action.ROLLBACK
        clock.advance()

        assert self.health_action(source) is Action.ALERT
        assert events == ["loaded"]
        assert source.loaded_at >= before

    def test_the_file_is_not_looked_at_more_often_than_the_interval(self, tmp_path, clock):
        events = []
        path, source = self.source(tmp_path, clock, events, interval=5.0)
        self.write(path, self.ALERT_ON_HEALTH)
        clock.advance(4.9)

        assert self.health_action(source) is Action.ROLLBACK

        clock.advance(0.2)
        assert self.health_action(source) is Action.ALERT

    def test_an_unchanged_file_is_not_reloaded(self, tmp_path, clock):
        events = []
        _, source = self.source(tmp_path, clock, events)

        for _ in range(3):
            clock.advance()
            source.policy

        assert events == []

    def test_an_invalid_file_keeps_the_policy_in_force_and_is_reported_once(self, tmp_path, clock):
        events = []
        path, source = self.source(tmp_path, clock, events)

        self.write(path, {"timeout": {"action": "retry"}})
        for _ in range(3):
            clock.advance()
            assert self.health_action(source) is Action.ROLLBACK

        assert events == ["rejected"]

    def test_fixing_a_rejected_file_loads_it(self, tmp_path, clock):
        events = []
        path, source = self.source(tmp_path, clock, events)
        self.write(path, {"timeout": {"action": "retry"}})
        clock.advance()
        source.policy

        self.write(path, self.ALERT_ON_HEALTH)
        clock.advance()

        assert self.health_action(source) is Action.ALERT
        assert events == ["rejected", "loaded"]

    def test_unparsable_yaml_is_rejected_too(self, tmp_path, clock):
        events = []
        path, source = self.source(tmp_path, clock, events)

        path.write_text("default: [unclosed")
        clock.advance()

        assert self.health_action(source) is Action.ROLLBACK
        assert events == ["rejected"]

    def test_a_file_that_disappears_keeps_the_policy_and_is_loaded_again_when_it_returns(self, tmp_path, clock):
        events = []
        path, source = self.source(tmp_path, clock, events)

        path.unlink()
        clock.advance()
        assert self.health_action(source) is Action.ROLLBACK
        assert events == []

        self.write(path, self.ALERT_ON_HEALTH)
        clock.advance()

        assert self.health_action(source) is Action.ALERT

    def test_an_interval_of_zero_turns_reloading_off(self, tmp_path, clock):
        events = []
        path, source = self.source(tmp_path, clock, events, interval=0)

        self.write(path, self.ALERT_ON_HEALTH)
        clock.advance(10_000)

        assert self.health_action(source) is Action.ROLLBACK

    def test_a_symlink_swap_like_a_mounted_configmap_is_detected(self, tmp_path, clock):
        from src.policy import PolicySource

        first, second = tmp_path / "v1", tmp_path / "v2"
        first.mkdir(), second.mkdir()
        self.write(first / "policy.yaml")
        self.write(second / "policy.yaml", self.ALERT_ON_HEALTH)
        data = tmp_path / "..data"
        data.symlink_to(first)
        mounted = tmp_path / "policy.yaml"
        mounted.symlink_to(data / "policy.yaml")
        events = []
        source = PolicySource(str(mounted), 5.0, events.append, clock=clock)

        staging = tmp_path / "..data_tmp"
        staging.symlink_to(second)
        os.replace(staging, data)
        clock.advance()

        assert self.health_action(source) is Action.ALERT
        assert events == ["loaded"]

    def test_many_threads_reloading_at_once_reload_once(self, tmp_path, clock):
        from concurrent.futures import ThreadPoolExecutor

        events = []
        path, source = self.source(tmp_path, clock, events)
        self.write(path, self.ALERT_ON_HEALTH)
        clock.advance()

        real_load = load_policy

        def slow_load(path):
            time.sleep(0.1)
            return real_load(path)

        with patch("src.policy.load_policy", slow_load), ThreadPoolExecutor(8) as pool:
            actions = list(pool.map(lambda _: self.health_action(source), range(32)))

        assert set(actions) == {Action.ALERT}
        assert events == ["loaded"]

    def test_an_invalid_file_at_start_up_stops_the_service(self, tmp_path):
        from src.policy import PolicySource

        path = tmp_path / "policy.yaml"
        path.write_text("default: {timeout: {action: retry}}")

        with pytest.raises(PolicyError):
            PolicySource(str(path))

    def test_a_swap_to_a_file_of_the_same_size_and_time_is_still_detected(self, tmp_path, clock):
        import json
        from src.policy import PolicySource

        old = json.dumps({"default": DEFAULT_POLICY["default"]})
        new = json.dumps({"default": self.ALERT_ON_HEALTH})
        size = max(len(old), len(new))
        first, second = tmp_path / "v1", tmp_path / "v2"
        first.mkdir(), second.mkdir()
        for directory, text in ((first, old), (second, new)):
            (directory / "policy.yaml").write_text(text.ljust(size))
            os.utime(directory / "policy.yaml", ns=(5_000_000_000, 5_000_000_000))
        data = tmp_path / "..data"
        data.symlink_to(first)
        mounted = tmp_path / "policy.yaml"
        mounted.symlink_to(data / "policy.yaml")
        source = PolicySource(str(mounted), 5.0, clock=clock)
        assert self.health_action(source) is Action.ROLLBACK

        staging = tmp_path / "..data_tmp"
        staging.symlink_to(second)
        os.replace(staging, data)
        clock.advance()

        assert self.health_action(source) is Action.ALERT

    def test_the_path_and_interval_are_exposed(self, tmp_path, clock):
        events = []
        path, source = self.source(tmp_path, clock, events, interval=7.0)

        assert (source.path, source.reload_seconds) == (str(path), 7.0)

    def test_reloading_without_a_listener_is_fine(self, tmp_path, clock):
        from src.policy import PolicySource

        path = tmp_path / "policy.yaml"
        self.write(path)
        source = PolicySource(str(path), 5.0, clock=clock)
        self.write(path, self.ALERT_ON_HEALTH)
        clock.advance()

        assert self.health_action(source) is Action.ALERT
