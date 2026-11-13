from unittest.mock import Mock

import pytest

from src.policy import Check
from src.prometheus import PrometheusError
from src.verification import (
    BREACH,
    ERROR,
    MISSING,
    OK,
    CheckResult,
    evaluate_checks,
    failed_checks,
    render_query,
    update_streaks,
)

ERRORS = Check(name="error-rate", query='rate(x{namespace="{{namespace}}"}[1m])', max=0.05)
LATENCY = Check(name="p95", query='q{pod=~"{{app}}-.+"}', max=0.5)
THROUGHPUT = Check(name="throughput", query='t{namespace="{{namespace}}"}', min=1)


def client_returning(*values):
    client = Mock()
    client.query.side_effect = list(values)
    return client


class TestRenderQuery:
    def test_both_placeholders_are_replaced_everywhere(self):
        query = 'a{namespace="{{namespace}}",pod=~"{{app}}-.+"} / b{namespace="{{namespace}}"}'

        assert render_query(query, "production", "sample-app") == (
            'a{namespace="production",pod=~"sample-app-.+"} / b{namespace="production"}'
        )

    def test_a_query_without_placeholders_is_unchanged(self):
        assert render_query("up", "production", "app") == "up"

    @pytest.mark.parametrize("value", [
        'prod"}) or vector(1) #', "a b", "a{b}", "back\\slash", "", "-leading", 'q"', "line\nbreak",
    ])
    @pytest.mark.parametrize("position", ["namespace", "app"])
    def test_a_value_that_could_break_out_of_a_label_matcher_is_refused(self, value, position):
        arguments = {"namespace": "production", "app": "app", position: value}

        with pytest.raises(ValueError, match="not safe"):
            render_query('x{namespace="{{namespace}}",pod=~"{{app}}"}', **arguments)

    def test_only_the_values_a_query_uses_are_checked(self):
        assert render_query('x{namespace="{{namespace}}"}', "production", "any app!") == 'x{namespace="production"}'

    @pytest.mark.parametrize("value", ["sample-app", "app.v2", "a_b", "Team1"])
    def test_ordinary_names_are_accepted(self, value):
        assert value in render_query("{{app}}", "production", value)


class TestEvaluateChecks:
    def run(self, client, checks=(ERRORS,), on_missing="ignore"):
        return evaluate_checks(checks, client, "production", "sample-app", on_missing)

    def test_a_value_inside_its_limit_is_ok(self):
        (result,) = self.run(client_returning(0.01))

        assert (result.name, result.result, result.value, result.detail) == ("error-rate", OK, 0.01, "0.01")
        assert result.counts_as_breach is False

    def test_a_value_above_a_maximum_breaches(self):
        (result,) = self.run(client_returning(0.2))

        assert (result.result, result.detail, result.counts_as_breach) == (BREACH, "0.2 > 0.05", True)

    def test_a_value_below_a_minimum_breaches(self):
        (result,) = self.run(client_returning(0.2), checks=(THROUGHPUT,))

        assert (result.result, result.detail) == (BREACH, "0.2 < 1")

    def test_a_value_on_the_limit_is_ok(self):
        assert self.run(client_returning(0.05))[0].result == OK

    def test_the_query_sent_is_the_rendered_one(self):
        client = client_returning(0)

        self.run(client)

        client.query.assert_called_once_with('rate(x{namespace="production"}[1m])')

    def test_no_data_is_missing_and_ignored_by_default(self):
        (result,) = self.run(client_returning(None))

        assert (result.result, result.counts_as_breach) == (MISSING, False)

    def test_no_data_counts_as_a_breach_when_asked(self):
        (result,) = self.run(client_returning(None), on_missing="breach")

        assert (result.result, result.counts_as_breach) == (MISSING, True)

    def test_a_failing_query_is_an_error_and_never_raises(self):
        client = Mock()
        client.query.side_effect = PrometheusError("cannot reach Prometheus: ConnectionError")

        (result,) = self.run(client)

        assert (result.result, result.value, result.counts_as_breach) == (ERROR, None, False)
        assert "cannot reach" in result.detail

    def test_an_error_counts_as_a_breach_when_asked(self):
        client = Mock()
        client.query.side_effect = PrometheusError("down")

        assert self.run(client, on_missing="breach")[0].counts_as_breach is True

    def test_an_unsafe_name_is_an_error_not_a_query(self):
        client = Mock()

        (result,) = evaluate_checks([ERRORS], client, 'prod"}', "app", "ignore")

        assert result.result == ERROR and "not safe" in result.detail
        client.query.assert_not_called()

    def test_one_failing_check_does_not_hide_the_others(self):
        client = Mock()
        client.query.side_effect = [PrometheusError("boom"), 0.9, 0.1]

        results = self.run(client, checks=(ERRORS, LATENCY, THROUGHPUT))

        assert [(r.name, r.result) for r in results] == [("error-rate", ERROR), ("p95", BREACH), ("throughput", BREACH)]


def result(name, kind, counts=None):
    return CheckResult(name, kind, None, "", kind == BREACH if counts is None else counts)


class TestStreaks:
    def test_a_breach_starts_and_extends_a_streak(self):
        streaks = update_streaks({}, [result("a", BREACH)])
        streaks = update_streaks(streaks, [result("a", BREACH)])

        assert streaks == {"a": 2}

    def test_a_healthy_value_resets_the_streak(self):
        assert update_streaks({"a": 2}, [result("a", OK)]) == {"a": 0}

    @pytest.mark.parametrize("kind", [MISSING, ERROR])
    def test_missing_data_and_errors_leave_the_streak_alone(self, kind):
        assert update_streaks({"a": 2}, [result("a", kind)]) == {"a": 2}

    @pytest.mark.parametrize("kind", [MISSING, ERROR])
    def test_missing_data_extends_the_streak_when_it_counts_as_a_breach(self, kind):
        assert update_streaks({"a": 1}, [result("a", kind, counts=True)]) == {"a": 2}

    def test_checks_are_counted_separately(self):
        streaks = update_streaks({"a": 1, "b": 1}, [result("a", BREACH), result("b", OK)])

        assert streaks == {"a": 2, "b": 0}

    def test_the_input_is_not_modified(self):
        original = {"a": 1}

        update_streaks(original, [result("a", BREACH)])

        assert original == {"a": 1}


class TestFailedChecks:
    def test_a_check_fails_at_the_threshold_not_before(self):
        assert failed_checks({"a": 2}, 3) == []
        assert failed_checks({"a": 3}, 3) == ["a"]

    def test_every_failing_check_is_named_in_order(self):
        assert failed_checks({"b": 5, "a": 3, "c": 1}, 3) == ["a", "b"]

    def test_no_streaks_no_failures(self):
        assert failed_checks({}, 1) == []
