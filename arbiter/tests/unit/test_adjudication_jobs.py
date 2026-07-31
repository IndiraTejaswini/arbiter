"""
Async adjudication queue semantics.

Adjudication used to run inline in the HTTP request, holding a Starlette
threadpool worker for the whole pipeline (~10 seconds with the LLM
advocates). With a 40-thread pool and a 15-connection database pool the
service saturated at roughly 4 QPS and then queued unboundedly with no
timeout and no backpressure. A client that gave up got no signal the work
was still running, and a retry started a second full adjudication of the
same case.

These tests pin the semantics that make the queue safe. They exercise the
pure logic -- state machine, backoff, terminality -- without a database;
the SQL-level guarantees (`SKIP LOCKED` claiming, the partial unique index
that permits at most one live job per case) are exercised by the migration
job in CI against a real Postgres, because they are properties of the
database and asserting them against a mock would prove nothing.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from arbiter.decision import jobs


def _job(**overrides) -> jobs.JobView:
    base = dict(
        job_id="11111111-1111-1111-1111-111111111111",
        case_id="22222222-2222-2222-2222-222222222222",
        state=jobs.QUEUED,
        attempts=0,
        max_attempts=3,
        requested_by="reviewer-1",
        error=None,
        decision_id=None,
        enqueued_at=datetime(2026, 3, 1, tzinfo=timezone.utc),
        started_at=None,
        finished_at=None,
    )
    base.update(overrides)
    return jobs.JobView(**base)  # type: ignore[arg-type]


# -- State machine --------------------------------------------------------


def test_the_state_set_is_closed():
    assert {jobs.QUEUED, jobs.RUNNING, jobs.SUCCEEDED, jobs.FAILED} == {
        "QUEUED", "RUNNING", "SUCCEEDED", "FAILED",
    }


def test_only_succeeded_and_failed_are_terminal():
    """A client polls until `terminal`. Marking RUNNING terminal would make
    it stop polling while the work was still in flight."""
    assert jobs.TERMINAL_STATES == frozenset({jobs.SUCCEEDED, jobs.FAILED})
    assert not _job(state=jobs.QUEUED).terminal
    assert not _job(state=jobs.RUNNING).terminal
    assert _job(state=jobs.SUCCEEDED).terminal
    assert _job(state=jobs.FAILED).terminal


def test_a_job_serialises_everything_a_client_needs_to_act():
    payload = _job(state=jobs.FAILED, error="boom", attempts=3).to_dict()
    for key in ("job_id", "case_id", "state", "terminal", "attempts", "max_attempts", "error"):
        assert key in payload
    assert payload["terminal"] is True
    assert payload["error"] == "boom"


# -- Backoff --------------------------------------------------------------


def test_backoff_grows_and_is_capped():
    """A deterministically-failing case would otherwise spin a worker at
    full speed until it exhausted max_attempts."""
    delays = [
        min(jobs._BACKOFF_CAP_SECONDS, jobs._BACKOFF_BASE_SECONDS * (2 ** (attempt - 1)))
        for attempt in range(1, 12)
    ]
    assert delays[0] == jobs._BACKOFF_BASE_SECONDS
    assert delays == sorted(delays), "backoff must be monotonically non-decreasing"
    assert max(delays) == jobs._BACKOFF_CAP_SECONDS, "backoff must be capped"


def test_backoff_is_measured_in_seconds_not_minutes():
    """A case sitting in a queue is a regulatory clock running. Retrying an
    adjudication half an hour later is a compliance cost, not just latency."""
    assert jobs._BACKOFF_BASE_SECONDS <= 60
    assert jobs._BACKOFF_CAP_SECONDS <= 900


# -- Abandoned work -------------------------------------------------------


def test_stale_window_is_generous_relative_to_the_pipeline():
    """The pipeline is ~10 seconds. The stale window must be far larger, or
    a slow LLM call gets mistaken for a dead worker and the case is
    adjudicated twice."""
    assert jobs.STALE_RUNNING_AFTER >= timedelta(minutes=5)


def test_job_already_queued_carries_the_existing_job():
    """Enqueueing a case that already has live work is not an error from
    the caller's point of view -- the thing they asked for is happening, so
    the API hands back the job doing it rather than a bare conflict."""
    existing = _job(state=jobs.RUNNING)
    error = jobs.JobAlreadyQueued(existing)
    assert error.existing is existing
    assert existing.case_id in str(error)


# -- Terminal outcomes ----------------------------------------------------


def test_a_permanently_failed_job_is_visible_not_swallowed():
    """A case that silently never adjudicates while its Reg Z clock runs is
    worse than one that is loudly broken."""
    failed = _job(state=jobs.FAILED, attempts=3, error="ValueError: bad rulepack")
    assert failed.terminal
    assert failed.error
    assert failed.to_dict()["state"] == "FAILED"


def test_a_succeeded_job_points_at_the_decision_it_produced():
    succeeded = _job(state=jobs.SUCCEEDED, decision_id="33333333-3333-3333-3333-333333333333")
    assert succeeded.to_dict()["decision_id"] == "33333333-3333-3333-3333-333333333333"


@pytest.mark.parametrize("state", [jobs.QUEUED, jobs.RUNNING])
def test_non_terminal_jobs_have_no_decision_yet(state):
    assert _job(state=state).decision_id is None
