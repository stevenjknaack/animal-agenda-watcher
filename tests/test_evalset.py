import pytest

from agenda_watcher import classify, evalset, store
from agenda_watcher.judge import Review
from conftest import event, item, matter
from fakes import FakeJudge, FakeLegistar


def flag(conn, flagged_ids, n, titles=None):
    """n matters; those in flagged_ids end with a relevant stage 2 verdict."""
    for i in range(1, n + 1):
        store.upsert_matter(conn, "madison", matter(i, MatterTitle=(titles or {}).get(i, f"Title {i}")))
    classify.run_triage(conn, FakeJudge(keep=set(flagged_ids)))
    classify.run_review(conn, FakeLegistar(), FakeJudge(), None)


def test_scores_precision_and_recall(conn):
    flag(conn, {1, 2, 3, 4, 5}, 7)
    for i in (1, 2, 3):
        evalset.record_label(conn, "madison", i, True, "animal_agriculture", "flagged")
    for i in (4, 5):
        evalset.record_label(conn, "madison", i, False, None, "flagged")
    evalset.record_label(conn, "madison", 6, True, "wildlife_management", "keyword")
    s = evalset.scores(conn)
    assert (s.labelled, s.true_pos, s.false_pos, s.false_neg) == (6, 3, 2, 1)
    assert s.precision == pytest.approx(0.6) and s.recall == pytest.approx(0.75)


def test_scores_none_when_nothing_labelled(conn):
    s = evalset.scores(conn)
    assert s.labelled == 0 and s.precision is None and s.recall is None and s.miss_rate_in_random_sample is None


def test_miss_rate_uses_only_the_random_sample(conn):
    flag(conn, set(), 6)
    evalset.record_label(conn, "madison", 1, True, None, "random", random_sample=True)
    for i in (2, 3, 4):
        evalset.record_label(conn, "madison", i, False, None, "random", random_sample=True)
    evalset.record_label(conn, "madison", 5, True, None, "keyword")
    s = evalset.scores(conn)
    assert s.random_sample_size == 4 and s.miss_rate_in_random_sample == pytest.approx(0.25)


def test_random_session_counts_an_already_labelled_item_without_asking_again(conn):
    flag(conn, set(), 2)
    evalset.record_label(conn, "madison", 1, True, "wildlife_management", "keyword")
    rows = evalset.sample_unflagged(conn, 10, seed=1)
    asked = []
    evalset.label_session(conn, rows, "random", ask=lambda p: asked.append(p) or "n", out=lambda s: None)
    assert len(asked) == 1
    s = evalset.scores(conn)
    assert s.random_sample_size == 2 and s.miss_rate_in_random_sample == pytest.approx(0.5)
    assert conn.execute("select source from labels where matter_id=1").fetchone()[0] == "keyword"


def test_sample_unflagged_excludes_items_not_fully_classified(conn):
    flag(conn, set(), 3)
    store.upsert_matter(conn, "madison", matter(50))                       # never classified
    store.upsert_matter(conn, "madison", matter(51))
    classify.run_triage(conn, FakeJudge(keep={50, 51}))                    # 50, 51 awaiting review
    assert sorted(r["matter_id"] for r in evalset.sample_unflagged(conn, 10, seed=1)) == [1, 2, 3]


def test_keyword_hits_match_whole_words_with_plurals(conn):
    flag(conn, set(), 5, {1: "Contract for goose management", 2: "Furniture purchase", 3: "Further study",
                          4: "Permit for two hogs", 5: "Culling of deer in parks"})
    matters, lines = evalset.keyword_hits(conn, ["goose", "fur", "hog", "cull"])
    assert [r["matter_id"] for r in matters] == [1, 4, 5] and lines == []


def test_keyword_hits_include_agenda_lines_without_matter(conn):
    store.upsert_event(conn, "dane", event(1))
    store.upsert_event_item(conn, "dane", 1, item(10, None, "Discussion: rodeo at the county fair"))
    store.upsert_event_item(conn, "dane", 1, item(11, None, "Call to order"))
    store.upsert_event_item(conn, "dane", 1, item(12, 99, "Rodeo contract"))
    matters, lines = evalset.keyword_hits(conn, ["rodeo"])
    assert matters == [] and [r["event_item_id"] for r in lines] == [10]


def test_sample_unflagged_is_deterministic_excludes_flagged_and_keeps_labelled(conn):
    flag(conn, {1}, 30)
    evalset.record_label(conn, "madison", 2, False, None, "keyword")
    a = [r["matter_id"] for r in evalset.sample_unflagged(conn, 10, seed=7)]
    b = [r["matter_id"] for r in evalset.sample_unflagged(conn, 10, seed=7)]
    assert a == b and len(a) == 10 and 1 not in a
    assert 2 in [r["matter_id"] for r in evalset.sample_unflagged(conn, 29, seed=7)]
    assert a != [r["matter_id"] for r in evalset.sample_unflagged(conn, 10, seed=8)]


def test_sample_unflagged_returns_all_when_fewer_than_n(conn):
    flag(conn, set(), 3)
    assert len(evalset.sample_unflagged(conn, 10, seed=1)) == 3


def test_human_label_overrides_model_in_final_view(conn):
    flag(conn, {1}, 2)
    evalset.record_label(conn, "madison", 1, False, None, "flagged")
    evalset.record_label(conn, "madison", 2, True, "captive_animals", "keyword")
    rows = {r["matter_id"]: (r["flagged"], r["relevant"], r["category"])
            for r in conn.execute("select * from final")}
    assert rows == {1: (1, 0, None), 2: (0, 1, "captive_animals")}


def test_label_session_records_answers_and_stops_on_q(conn):
    flag(conn, {1, 2, 3, 4}, 4)
    rows = evalset.unlabelled_flagged(conn)
    answers = iter(["y", "n", "s", "q"])
    shown = []
    done = evalset.label_session(conn, rows, "flagged", ask=lambda prompt: next(answers), out=shown.append)
    assert done == 2
    assert [tuple(r) for r in conn.execute("select matter_id, relevant, category, source from labels order by 1")] == [
        (1, 1, "animal_agriculture", "flagged"), (2, 0, None, "flagged")]
    assert any("Title 1" in s for s in shown)
