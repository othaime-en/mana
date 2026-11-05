"""Recovery policy: which action follows which failure, as data plus one pure function.

A policy maps each failure type to a rule. A retry rule allows a number of
attempts and names what happens after them. Overrides change rules for one
namespace or application. decide() turns a rule and the facts of a failure
into a decision without touching Redis, Kubernetes or GitHub, so every cell of
the policy can be tested exhaustively.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Dict, List, Optional

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from src.diagnosis import Diagnosis

FAILURE_TYPES = (
    "build_failure",
    "test_failure",
    "deployment_failure",
    "health_check_failure",
    "smoke_test_failure",
    "timeout",
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
        _check_failure_types(self.rules, require_all=False)
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


def _check_failure_types(rules: Dict[str, Rule], require_all: bool) -> None:
    unknown = sorted(set(rules) - set(FAILURE_TYPES))
    if unknown:
        raise ValueError(f"unknown failure type(s) {unknown}; valid: {list(FAILURE_TYPES)}")
    if require_all:
        missing = [name for name in FAILURE_TYPES if name not in rules]
        if missing:
            raise ValueError(f"the default policy must cover every failure type; missing {missing}")
    for name, rule in rules.items():
        if name in NON_ROLLBACK_FAILURES and rule.rolls_back():
            raise ValueError(
                f"{name} happens before anything is deployed, so its rule cannot roll back"
            )


class Policy(_Strict):
    version: int = Field(default=1)
    default: Dict[str, Rule]
    overrides: List[Override] = Field(default_factory=list)
    rollback: RollbackSettings = Field(default_factory=RollbackSettings)
    circuit_breaker: CircuitBreaker = Field(default_factory=CircuitBreaker)

    @model_validator(mode="after")
    def _check(self) -> "Policy":
        if self.version != 1:
            raise ValueError(f"unsupported policy version {self.version}; expected 1")
        _check_failure_types(self.default, require_all=True)
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

