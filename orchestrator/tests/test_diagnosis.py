from types import SimpleNamespace as NS

import pytest

from src.diagnosis import (
    DETERMINISTIC_REASONS,
    TRANSIENT_REASONS,
    Diagnosis,
    diagnose,
    diagnose_deployment,
    diagnose_pods,
)


def container_status(waiting=None, terminated=None, last_terminated=None, name="app"):
    return NS(
        name=name,
        state=NS(
            waiting=NS(reason=waiting, message="") if waiting else None,
            terminated=NS(reason=terminated) if terminated else None,
        ),
        last_state=NS(terminated=NS(reason=last_terminated) if last_terminated else None),
    )


def pod(image="registry/app:v2", statuses=(), conditions=(), name="app-1"):
    return NS(
        metadata=NS(name=name),
        spec=NS(containers=[NS(image=image)]),
        status=NS(container_statuses=list(statuses), conditions=list(conditions)),
    )


def deployment(*conditions):
    return NS(status=NS(conditions=list(conditions)))


def condition(type_, status, reason, message=""):
    return NS(type=type_, status=status, reason=reason, message=message)


@pytest.mark.parametrize("reason", sorted(DETERMINISTIC_REASONS - {"OOMKilled", "ProgressDeadlineExceeded"}))
def test_a_container_that_cannot_start_is_deterministic(reason):
    result = diagnose_pods([pod(statuses=[container_status(waiting=reason)])])

    assert result.reason == reason
    assert result.transient is False


@pytest.mark.parametrize("reason", ["ImagePullBackOff", "ErrImagePull"])
def test_an_image_that_cannot_be_pulled_is_transient(reason):
    result = diagnose_pods([pod(statuses=[container_status(waiting=reason)])])

    assert result == Diagnosis(reason, True, "app-1/app:")


def test_a_pod_no_node_can_take_is_transient():
    unschedulable = condition("PodScheduled", "False", "Unschedulable", "0/3 nodes are available")

    result = diagnose_pods([pod(conditions=[unschedulable])])

    assert result.reason == "Unschedulable"
    assert result.transient is True
    assert "0/3 nodes" in result.detail


def test_a_scheduled_pod_is_not_unschedulable():
    scheduled = condition("PodScheduled", "True", "")

    assert diagnose_pods([pod(conditions=[scheduled])]) is None


@pytest.mark.parametrize("status", [
    container_status(last_terminated="OOMKilled"),
    container_status(terminated="OOMKilled"),
])
def test_running_out_of_memory_is_deterministic(status):
    result = diagnose_pods([pod(statuses=[status])])

    assert result.reason == "OOMKilled"
    assert result.transient is False


@pytest.mark.parametrize("waiting", ["ContainerCreating", "PodInitializing"])
def test_normal_start_up_states_are_not_failures(waiting):
    assert diagnose_pods([pod(statuses=[container_status(waiting=waiting)])]) is None


def test_a_terminated_container_with_another_reason_is_not_oom():
    assert diagnose_pods([pod(statuses=[container_status(terminated="Completed")])]) is None


def test_pods_of_the_previous_revision_do_not_count():
    old_pod = pod(image="registry/app:v1", statuses=[container_status(waiting="CrashLoopBackOff")])
    new_pod = pod(image="registry/app:v2", statuses=[])

    assert diagnose_pods([old_pod, new_pod], version="v2") is None


def test_without_a_version_every_pod_counts():
    old_pod = pod(image="registry/app:v1", statuses=[container_status(waiting="CrashLoopBackOff")])

    assert diagnose_pods([old_pod]).reason == "CrashLoopBackOff"


def test_a_digest_pinned_pod_never_matches_a_version():
    pinned = pod(image="registry/app@sha256:abc", statuses=[container_status(waiting="CrashLoopBackOff")])

    assert diagnose_pods([pinned], version="v2") is None


def test_a_deterministic_cause_wins_over_a_transient_one():
    pulling = pod(statuses=[container_status(waiting="ImagePullBackOff")], name="a")
    crashing = pod(statuses=[container_status(waiting="CrashLoopBackOff")], name="b")

    assert diagnose_pods([pulling, crashing]).reason == "CrashLoopBackOff"
    assert diagnose_pods([crashing, pulling]).reason == "CrashLoopBackOff"


def test_healthy_pods_give_no_diagnosis():
    assert diagnose_pods([pod(statuses=[container_status()]), pod()]) is None


def test_pods_with_missing_status_fields_are_tolerated():
    bare = NS(metadata=NS(name="x"), spec=NS(containers=[NS(image="r/a:v2")]), status=None)
    empty = NS(metadata=None, spec=None, status=NS(container_statuses=None, conditions=None))

    assert diagnose_pods([bare, empty]) is None
    assert diagnose_pods([bare], version="v2") is None


def test_an_exhausted_progress_deadline_is_deterministic():
    exceeded = condition("Progressing", "False", "ProgressDeadlineExceeded", "timed out")

    result = diagnose_deployment(deployment(exceeded))

    assert result == Diagnosis("ProgressDeadlineExceeded", False, "timed out")


@pytest.mark.parametrize("progressing", [
    condition("Progressing", "True", "ReplicaSetUpdated"),
    condition("Progressing", "True", "NewReplicaSetAvailable"),
    condition("Available", "False", "MinimumReplicasUnavailable"),
    condition("Progressing", "True", "ProgressDeadlineExceeded"),
])
def test_other_deployment_conditions_are_not_a_diagnosis(progressing):
    assert diagnose_deployment(deployment(progressing)) is None


def test_a_deployment_without_status_is_tolerated():
    assert diagnose_deployment(NS(status=None)) is None
    assert diagnose_deployment(deployment()) is None


def test_pod_signals_take_precedence_over_the_deployment_condition():
    exceeded = condition("Progressing", "False", "ProgressDeadlineExceeded")
    pulling = pod(statuses=[container_status(waiting="ImagePullBackOff")])

    assert diagnose([pulling], deployment(exceeded), "v2").reason == "ImagePullBackOff"


def test_the_deployment_condition_is_used_when_pods_say_nothing():
    exceeded = condition("Progressing", "False", "ProgressDeadlineExceeded")

    assert diagnose([pod()], deployment(exceeded), "v2").reason == "ProgressDeadlineExceeded"
    assert diagnose([], deployment(), "v2") is None


def test_metric_reasons_are_bounded():
    assert Diagnosis("CrashLoopBackOff", False).metric_reason == "CrashLoopBackOff"
    assert Diagnosis("SomethingNew", False).metric_reason == "other"


def test_no_reason_is_both_transient_and_deterministic():
    assert not TRANSIENT_REASONS & DETERMINISTIC_REASONS
