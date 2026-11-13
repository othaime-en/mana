"""Recovery policy: which action follows which failure, as data plus one pure function.

A policy maps each failure type to a rule. A retry rule allows a number of
attempts and names what happens after them. Overrides change rules for one
namespace or application. decide() turns a rule and the facts of a failure
into a decision without touching Redis, Kubernetes or GitHub, so every cell of
the policy can be tested exhaustively.
"""

import logging
import os
import re
import threading
import time
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Dict, List, Literal, Optional, Tuple

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from src.diagnosis import Diagnosis

logger = logging.getLogger(__name__)

FAILURE_TYPES = (
    "build_failure",
    "test_failure",
    "deployment_failure",
    "health_check_failure",
    "smoke_test_failure",
    "timeout",
    "rollout_stuck",
    "verification_failure",
)
NON_ROLLBACK_FAILURES = frozenset({"build_failure", "test_failure"})


class Action(str, Enum):
    """What Mana does about a failure"""
    RETRY = "retry"
    ROLLBACK = "rollback"
    ALERT = "alert"


class Reason(str, Enum):
    """Why a decision came out as it did; a bounded set, safe as a metric label"""
    POLICY = "policy"
    RETRIES_EXHAUSTED = "retries_exhausted"
    RETRY_UNAVAILABLE = "retry_unavailable"
    DETERMINISTIC = "deterministic_failure"
    NO_ROLLBACK_TARGET = "no_rollback_target"


class PolicyError(ValueError):
    """The policy file cannot be read or is not a valid policy"""


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Rule(_Strict):
    """The action for one failure type"""
    action: Action
    max_attempts: Optional[int] = Field(default=None, ge=1)
    then: Action = Action.ALERT

    @model_validator(mode="after")
    def _check_shape(self) -> "Rule":
        if self.action is Action.RETRY:
            if self.max_attempts is None:
                raise ValueError("a retry rule needs max_attempts")
            if self.then is Action.RETRY:
                raise ValueError("'then' must be rollback or alert")
        elif self.max_attempts is not None or "then" in self.model_fields_set:
            raise ValueError("max_attempts and then only apply to retry rules")
        return self

    def capped(self, ceiling: int) -> "Rule":
        """The same rule with at most ceiling retries (MAX_RETRIES stays a global ceiling)"""
        if self.action is Action.RETRY and self.max_attempts > ceiling:
            return self.model_copy(update={"max_attempts": ceiling})
        return self

    def rolls_back(self) -> bool:
        return self.action is Action.ROLLBACK or (
            self.action is Action.RETRY and self.then is Action.ROLLBACK
        )


class Match(_Strict):
    """Selects the namespace and application an override applies to; unset matches all"""
    namespace: Optional[str] = None
    app: Optional[str] = None

    def applies(self, namespace: str, app: str) -> bool:
        return (self.namespace in (None, namespace)) and (self.app in (None, app))


class Override(_Strict):
    """Rules for one namespace or application; the key is not `on`, which YAML 1.1 reads as true"""
    match: Match
    rules: Dict[str, Rule]

    @model_validator(mode="after")
    def _check_rules(self) -> "Override":
        if not self.rules:
            raise ValueError("an override needs at least one rule under 'rules'")
        _check_failure_types(self.rules)
        return self


class RollbackSettings(_Strict):
    """How a rollback is verified, and whether the failed version is frozen afterwards"""
    verify_timeout_seconds: int = Field(default=120, ge=10, le=3600)
    freeze_failed_version: bool = True
    deny_ttl_seconds: int = Field(default=604800, ge=60)


class CircuitBreaker(_Strict):
    """Pause automation for an application that keeps rolling back"""
    max_rollbacks: int = Field(default=3, ge=1)
    window_seconds: int = Field(default=3600, ge=60)


PLACEHOLDERS = ("{{namespace}}", "{{app}}")


class Check(_Strict):
    """One PromQL expression that must stay on the right side of a threshold"""
    name: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,39}$")
    query: str = Field(min_length=1, max_length=2000)
    max: Optional[float] = None
    min: Optional[float] = None

    @model_validator(mode="after")
    def _check_shape(self) -> "Check":
        if (self.max is None) == (self.min is None):
            raise ValueError("a check needs exactly one of max and min")
        if not any(placeholder in self.query for placeholder in PLACEHOLDERS):
            raise ValueError(
                f"the query must use {{{{namespace}}}} or {{{{app}}}}, or it measures every application"
            )
        return self

    def breached_by(self, value: float) -> bool:
        return value > self.max if self.max is not None else value < self.min


class Verification(_Strict):
    """
    Check a deployment against Prometheus before it counts as a rollback target

    After the pipeline reports success the deployment is held in `verifying`.
    Nothing is evaluated during warmup_seconds, so the old pods' samples leave
    the rate windows. Then every interval_seconds each check is evaluated; a
    check that breaches consecutive_breaches times in a row fails the
    deployment (a verification_failure). If bake_seconds pass without that, the
    deployment succeeds and its version becomes the rollback target.
    """
    enabled: bool = False
    namespaces: List[str] = Field(default_factory=lambda: ["production"], min_length=1)
    warmup_seconds: int = Field(default=60, ge=0, le=3600)
    bake_seconds: int = Field(default=180, ge=10, le=7200)
    interval_seconds: int = Field(default=15, ge=1, le=600)
    consecutive_breaches: int = Field(default=3, ge=1, le=50)
    on_missing: Literal["ignore", "breach"] = "ignore"
    checks: List[Check] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_timing_and_checks(self) -> "Verification":
        if self.warmup_seconds >= self.bake_seconds:
            raise ValueError("warmup_seconds must be less than bake_seconds")
        window = self.bake_seconds - self.warmup_seconds
        if (self.consecutive_breaches - 1) * self.interval_seconds > window:
            raise ValueError(
                "consecutive_breaches evaluations do not fit between warmup and bake, "
                "so a failure could never be reached"
            )
        names = [check.name for check in self.checks]
        if len(set(names)) != len(names):
            raise ValueError("check names must be unique")
        if self.enabled and not self.checks:
            raise ValueError("verification is enabled but lists no checks")
        for namespace in self.namespaces:
            if not re.fullmatch(r"[a-z0-9]([-a-z0-9]*[a-z0-9])?", namespace):
                raise ValueError(f"invalid namespace: {namespace!r}")
        return self

    def applies_to(self, namespace: str) -> bool:
        return self.enabled and namespace in self.namespaces


def _check_failure_types(rules: Dict[str, Rule]) -> None:
    unknown = sorted(set(rules) - set(FAILURE_TYPES))
    if unknown:
        raise ValueError(f"unknown failure type(s) {unknown}; valid: {list(FAILURE_TYPES)}")
    for name, rule in rules.items():
        if name in NON_ROLLBACK_FAILURES and rule.rolls_back():
            raise ValueError(
                f"{name} happens before anything is deployed, so its rule cannot roll back"
            )


class Policy(_Strict):
    """The recovery policy; failure types missing from `default` use the built-in rule"""
    version: int = Field(default=1)
    default: Dict[str, Rule]
    overrides: List[Override] = Field(default_factory=list)
    rollback: RollbackSettings = Field(default_factory=RollbackSettings)
    circuit_breaker: CircuitBreaker = Field(default_factory=CircuitBreaker)
    verification: Verification = Field(default_factory=Verification)

    @model_validator(mode="before")
    @classmethod
    def _fill_default_rules(cls, data):
        """A file written before a failure type existed keeps working: the built-in rule fills the gap"""
        if isinstance(data, dict):
            given = data.get("default")
            if given is None or isinstance(given, dict):
                return {**data, "default": {**DEFAULT_POLICY["default"], **(given or {})}}
        return data

    @model_validator(mode="after")
    def _check(self) -> "Policy":
        if self.version != 1:
            raise ValueError(f"unsupported policy version {self.version}; expected 1")
        _check_failure_types(self.default)
        return self

    def rule_for(self, namespace: str, app: str, failure_type: str) -> Rule:
        """The first matching override that defines the failure type, else the default"""
        for override in self.overrides:
            if override.match.applies(namespace, app) and failure_type in override.rules:
                return override.rules[failure_type]
        return self.default[failure_type]


DEFAULT_POLICY: Dict = {
    "version": 1,
    "default": {
        "build_failure": {"action": "retry", "max_attempts": 2},
        "test_failure": {"action": "alert"},
        "deployment_failure": {"action": "retry", "max_attempts": 1, "then": "rollback"},
        "health_check_failure": {"action": "rollback"},
        "smoke_test_failure": {"action": "rollback"},
        "timeout": {"action": "retry", "max_attempts": 1, "then": "rollback"},
        "rollout_stuck": {"action": "rollback"},
        "verification_failure": {"action": "rollback"},
    },
}


def load_policy(path: str = "") -> Policy:
    """Load the policy file, or the built-in default policy when no path is given"""
    if not path:
        return Policy.model_validate(DEFAULT_POLICY)
    try:
        with open(path, encoding="utf-8") as handle:
            document = yaml.safe_load(handle)
    except OSError as error:
        raise PolicyError(f"cannot read policy file {path}: {error}") from error
    except yaml.YAMLError as error:
        raise PolicyError(f"policy file {path} is not valid YAML: {error}") from error
    if not isinstance(document, dict):
        raise PolicyError(f"policy file {path} must contain a mapping")
    try:
        return Policy.model_validate(document)
    except ValidationError as error:
        details = "; ".join(
            f"{'.'.join(str(part) for part in item['loc'])}: {item['msg']}" for item in error.errors()
        )
        raise PolicyError(f"invalid policy in {path}: {details}") from error


@dataclass(frozen=True)
class Decision:
    """What to do about a failure, and why"""
    action: Action
    reason: Reason
    detail: str = ""


def decide(
    rule: Rule,
    attempt: int,
    *,
    can_retry: bool,
    has_rollback_target: bool,
    diagnosis: Optional[Diagnosis] = None
) -> Decision:
    """
    Choose the action for one failure

    attempt is the number of failures seen for this deployment, this one
    included. A retry rule falls through to its 'then' action once the attempts
    are used up, when a retry is not possible (no workflow run to re-run), or
    when the diagnosis says the cause is deterministic and would only fail
    again. A rollback without a target becomes an alert.
    """
    action, reason, detail = rule.action, Reason.POLICY, ""

    if action is Action.RETRY:
        if diagnosis is not None and not diagnosis.transient:
            action, reason = rule.then, Reason.DETERMINISTIC
            detail = f"{diagnosis.reason} would fail again"
        elif not can_retry:
            action, reason = rule.then, Reason.RETRY_UNAVAILABLE
            detail = "there is no workflow run to re-run"
        elif attempt > rule.max_attempts:
            action, reason = rule.then, Reason.RETRIES_EXHAUSTED
            detail = f"{rule.max_attempts} retries used"

    if action is Action.ROLLBACK and not has_rollback_target:
        return Decision(Action.ALERT, Reason.NO_ROLLBACK_TARGET, "no earlier version is known")

    return Decision(action, reason, detail)


class PolicySource:
    """
    The policy in force, reloaded when its file changes

    A reload that fails validation keeps the policy that was running and is
    reported through on_reload("rejected"), so a bad edit can neither crash a
    running orchestrator nor silently change its behaviour. The file is checked
    at most every reload_seconds (0 turns reloading off). A mounted ConfigMap
    changes by swapping a symlink, which stat() follows, so the file's inode,
    size and modification time identify a change.
    """

    def __init__(
        self,
        path: str = "",
        reload_seconds: float = 5.0,
        on_reload: Optional[Callable[[str], None]] = None,
        *,
        policy: Optional[Policy] = None,
        clock: Callable[[], float] = time.monotonic
    ):
        self._path = "" if policy is not None else path
        self._interval = reload_seconds
        self._on_reload = on_reload
        self._clock = clock
        self._lock = threading.Lock()
        self._policy = policy if policy is not None else load_policy(path)
        self._signature = self._stat()
        self._checked = clock()
        self.loaded_at = time.time()

    @property
    def path(self) -> str:
        return self._path

    @property
    def reload_seconds(self) -> float:
        return self._interval

    @property
    def policy(self) -> Policy:
        self._refresh()
        return self._policy

    def _stat(self) -> Optional[Tuple[int, int, int]]:
        if not self._path:
            return None
        try:
            status = os.stat(self._path)
        except OSError:
            return None
        return status.st_mtime_ns, status.st_size, status.st_ino

    def _refresh(self) -> None:
        if not self._path or self._interval <= 0:
            return
        if self._clock() - self._checked < self._interval:
            return
        with self._lock:
            if self._clock() - self._checked < self._interval:
                return
            try:
                self._reload_if_changed()
            finally:
                self._checked = self._clock()

    def _reload_if_changed(self) -> None:
        signature = self._stat()
        if signature is None or signature == self._signature:
            return
        self._signature = signature
        try:
            reloaded = load_policy(self._path)
        except PolicyError as error:
            logger.error("Policy reload rejected, keeping the policy in force: %s", error)
            self._report("rejected")
            return
        self._policy = reloaded
        self.loaded_at = time.time()
        logger.info("Policy reloaded from %s", self._path)
        self._report("loaded")

    def _report(self, outcome: str) -> None:
        if self._on_reload:
            self._on_reload(outcome)
