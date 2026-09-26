"""Seal pressure semantics: readiness is live, a relief resets it.

These tests pin down the three things the seal has to promise the plant:
pressure below the minimum is never "ready", relieving the seal clears the
established state until it is built up again, and a seal that loses pressure
after establishing raises a visible alarm.
"""

from __future__ import annotations

import pytest

from line_control.runtime.errors import GateBlockedError, LimitViolationError

from tests.support import feed_ticket, prime_for_feed


def test_establish_records_the_pressure_and_readiness(line, unit) -> None:
    status = line.seal.establish(unit, 30)

    assert status.established is True
    assert status.pressure == 30
    assert status.minimum == 20
    assert status.margin == 10
    assert status.ready is True
    assert status.alarm is False


def test_establish_below_the_minimum_leaves_no_state_behind(line, unit) -> None:
    with pytest.raises(LimitViolationError) as refusal:
        line.seal.establish(unit, 10)

    assert refusal.value.context["low"] == 20
    status = line.seal.status(unit)
    assert status.established is False
    assert status.pressure == 0
    assert status.ready is False


def test_relieve_clears_establishment_until_the_seal_is_rebuilt(line, unit) -> None:
    line.seal.establish(unit, 30)
    assert line.seal.status(unit).ready is True

    relieved = line.seal.relieve(unit)

    assert relieved.established is False
    assert relieved.pressure == 0
    assert relieved.ready is False
    assert relieved.alarm is False

    rebuilt = line.seal.establish(unit, 25)
    assert rebuilt.established is True
    assert rebuilt.ready is True


def test_relieved_seal_closes_the_feed_gate(line, unit) -> None:
    prime_for_feed(line, unit)
    assert line.board.gates.require("feed_open", unit).open is True

    line.seal.relieve(unit)

    verdict = line.board.gates.evaluate("feed_open", unit)
    assert verdict.open is False
    assert verdict.blocked_by == ("seal_established",)

    with pytest.raises(GateBlockedError):
        line.open_feed(unit, feed_ticket(line, unit))


def test_a_pressure_drop_after_establishing_raises_an_alarm(line, unit) -> None:
    line.seal.establish(unit, 30)

    status = line.seal.trim(unit, 10)

    assert status.pressure == 10
    assert status.established is True
    assert status.ready is False
    assert status.alarm is True
    assert status.alarm_reason == "seal pressure below minimum"
    assert "seal_established" in line.board.gates.evaluate("feed_open", unit).blocked_by
    assert line.readiness(unit).rule == "seal_low_pressure"


def test_recovered_pressure_clears_the_alarm(line, unit) -> None:
    line.seal.establish(unit, 30)
    line.seal.trim(unit, 10)
    assert line.seal.status(unit).alarm is True

    status = line.seal.trim(unit, 40)

    assert status.alarm is False
    assert status.alarm_reason == ""
    assert status.margin == 20
    assert status.ready is True


def test_zero_pressure_after_establishing_is_visible_as_an_alarm(line, unit) -> None:
    line.seal.establish(unit, 30)

    status = line.seal.trim(unit, 0)

    assert status.pressure == 0
    assert status.ready is False
    assert status.alarm is True


def test_relieved_seal_does_not_alarm(line, unit) -> None:
    line.seal.establish(unit, 30)

    status = line.seal.relieve(unit)

    assert status.ready is False
    assert status.alarm is False
    assert status.alarm_reason == ""
    assert line.readiness(unit).rule == "seal_missing"


def test_a_negative_trim_is_refused(line, unit) -> None:
    line.seal.establish(unit, 30)

    with pytest.raises(LimitViolationError):
        line.seal.trim(unit, -5)


def test_seal_state_survives_a_restart(tmp_path) -> None:
    from tests.support import open_line

    first = open_line(tmp_path, durable=True)
    first.register_unit("u1")
    first.seal.establish("u1", 30)

    assert open_line(tmp_path).seal.status("u1").ready is True

    first.seal.relieve("u1")

    reopened = open_line(tmp_path)
    status = reopened.seal.status("u1")
    assert status.established is False
    assert status.pressure == 0
    assert status.ready is False
    assert "seal_established" in reopened.board.gates.evaluate(
        "feed_open", "u1"
    ).blocked_by
