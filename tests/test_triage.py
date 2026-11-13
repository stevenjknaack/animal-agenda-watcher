from types import SimpleNamespace

import pytest

from agenda_watcher import classify, store
from agenda_watcher.judge import CATEGORIES, ClaudeJudge, JudgeError, TriageItem, TriageOut
from conftest import matter
from fakes import FakeJudge


def add(conn, n, client="madison", **over):
    for i in range(1, n + 1):
        store.upsert_matter(conn, client, matter(i, **over))


def stage1(conn):
    return [tuple(r) for r in conn.execute(
        "select matter_id, relevant, reason from verdicts where stage=1 order by client, matter_id")]


def test_triage_writes_stage1_verdicts_for_all_items(conn):
    add(conn, 3)
    assert classify.run_triage(conn, FakeJudge(keep={2})) == (3, 1)
    assert [(m, r) for m, r, _ in stage1(conn)] == [(1, 0), (2, 1), (3, 0)]


def test_triage_skips_matters_already_judged(conn):
    add(conn, 3)
    classify.run_triage(conn, FakeJudge(keep={2}))
    judge = FakeJudge()
    assert classify.run_triage(conn, judge) == (0, 0)
    assert judge.triage_calls == []


def test_triage_batches_by_batch_size(conn):
    add(conn, 120)
    judge = FakeJudge()
    classify.run_triage(conn, judge, batch_size=50)
    assert [len(c) for c in judge.triage_calls] == [50, 50, 20]


def test_triage_never_mixes_clients_in_a_batch(conn):
    add(conn, 2, "madison")
    add(conn, 2, "dane")
    judge = FakeJudge(keep={1})
    assert classify.run_triage(conn, judge) == (4, 2)
    assert [len(c) for c in judge.triage_calls] == [2, 2]


def test_judge_error_marks_whole_batch_as_survivors(conn):
    add(conn, 3)
    assert classify.run_triage(conn, FakeJudge(fail_triage=True)) == (3, 3)
    assert stage1(conn) == [(1, 1, "triage failed"), (2, 1, "triage failed"), (3, 1, "triage failed")]


def test_matter_with_empty_title_and_name_is_skipped_not_sent(conn):
    store.upsert_matter(conn, "madison", matter(1, MatterTitle=None, MatterName="  "))
    store.upsert_matter(conn, "madison", matter(2, MatterTitle=None, MatterName="Deer management contract"))
    judge = FakeJudge()
    assert classify.run_triage(conn, judge) == (1, 0)
    assert [(i.matter_id, i.title) for i in judge.triage_calls[0]] == [(2, "Deer management contract")]


def test_triage_date_window_limits_by_intro_date(conn):
    store.upsert_matter(conn, "madison", matter(1, MatterIntroDate="2025-01-10T00:00:00"))
    store.upsert_matter(conn, "madison", matter(2, MatterIntroDate="2025-02-10T00:00:00"))
    assert classify.run_triage(conn, FakeJudge(), since="2025-02-01", until="2025-02-28") == (1, 0)


class StubMessages:
    def __init__(self, result=None, error=None):
        self.result, self.error, self.calls = result, error, []

    def parse(self, **kw):
        self.calls.append(kw)
        if self.error:
            raise self.error
        return self.result


def claude(result=None, error=None):
    messages = StubMessages(result, error)
    return ClaudeJudge("triage-m", "review-m", client=SimpleNamespace(messages=messages)), messages


ITEMS = [TriageItem(1, "Rezoning for meat processing", "Ordinance", "PLAN COMMISSION"),
         TriageItem(2, "Approve minutes", "Minutes", "COMMON COUNCIL")]


def test_claude_triage_ignores_ids_not_in_the_batch():
    judge, messages = claude(SimpleNamespace(stop_reason="end_turn", parsed_output=TriageOut(keep=[2, 999])))
    assert judge.triage(ITEMS) == {2}
    assert messages.calls[0]["model"] == "triage-m"
    assert "Rezoning for meat processing" in messages.calls[0]["messages"][0]["content"]


def test_claude_triage_raises_judge_error_on_api_failure():
    judge, _ = claude(error=RuntimeError("network"))
    with pytest.raises(JudgeError):
        judge.triage(ITEMS)


@pytest.mark.parametrize("stop", ["refusal", "max_tokens"])
def test_claude_triage_raises_judge_error_on_incomplete_response(stop):
    judge, _ = claude(SimpleNamespace(stop_reason=stop, parsed_output=None))
    with pytest.raises(JudgeError):
        judge.triage(ITEMS)


def test_triage_prompt_states_scope_and_disguises():
    judge, messages = claude(SimpleNamespace(stop_reason="end_turn", parsed_output=TriageOut(keep=[])))
    judge.triage(ITEMS)
    system = messages.calls[0]["system"]
    for phrase in ("conditional use permit", "manure", "Henry Vilas Zoo", "foie gras", "When unsure, keep"):
        assert phrase in system
    assert len(CATEGORIES) == 6
