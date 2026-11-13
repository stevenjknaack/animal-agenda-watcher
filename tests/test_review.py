from pathlib import Path

import httpx

from agenda_watcher import classify, store
from agenda_watcher.judge import ClaudeJudge, Review, ReviewOut
from conftest import matter
from fakes import FakeJudge, FakeLegistar
from test_triage import claude
from types import SimpleNamespace

PDF = (Path(__file__).parent / "fixtures" / "small.pdf").read_bytes()


def http(status=200, content=PDF):
    return httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(status, content=content)))


def setup(conn, survivors=(2,), n=3):
    for i in range(1, n + 1):
        store.upsert_matter(conn, "madison", matter(i))
    classify.run_triage(conn, FakeJudge(keep=set(survivors)))


def stage2(conn):
    return [tuple(r) for r in conn.execute(
        "select matter_id, relevant, category, confidence, reason, summary from verdicts where stage=2 order by matter_id")]


def test_review_only_touches_stage1_survivors(conn):
    setup(conn, survivors=(2,))
    judge = FakeJudge()
    assert classify.run_review(conn, FakeLegistar(), judge, http()) == (1, 1)
    assert [c["title"] for c in judge.review_calls] == ["Title 2"]


def test_review_stores_category_confidence_reason_summary(conn):
    setup(conn)
    classify.run_review(conn, FakeLegistar(), FakeJudge(), http())
    assert stage2(conn) == [(2, 1, "animal_agriculture", 0.9, "because", "A summary.")]


def test_review_passes_matter_text_and_attachment_text_to_judge(conn):
    setup(conn)
    att = {"MatterAttachmentId": 5, "MatterAttachmentName": "Staff report", "MatterAttachmentHyperlink": "https://x/a.pdf"}
    judge = FakeJudge()
    classify.run_review(conn, FakeLegistar(text="the ordinance", attachments={2: [att]}), judge, http())
    call = judge.review_calls[0]
    assert call["text"] == "the ordinance"
    assert call["attachment_names"] == ["Staff report"]
    assert "slaughterhouse" in call["attachment_text"]


def test_review_uses_stored_attachments_without_asking_legistar(conn):
    setup(conn)
    store.upsert_attachment(conn, "madison", 2, {"MatterAttachmentId": 5, "MatterAttachmentName": "Map",
                                                 "MatterAttachmentHyperlink": "https://x/a.pdf"})
    lg = FakeLegistar()
    classify.run_review(conn, lg, FakeJudge(), http())
    assert lg.get_calls == []


def test_review_reads_at_most_max_attachments(conn):
    setup(conn)
    atts = [{"MatterAttachmentId": i, "MatterAttachmentName": f"A{i}", "MatterAttachmentHyperlink": f"https://x/{i}.pdf"}
            for i in range(1, 6)]
    judge = FakeJudge()
    classify.run_review(conn, FakeLegistar(attachments={2: atts}), judge, http(), max_attachments=3)
    call = judge.review_calls[0]
    assert len(call["attachment_names"]) == 5
    assert call["attachment_text"].count("slaughterhouse") == 3


def test_attachment_text_is_capped_at_30000_chars(conn):
    setup(conn)
    atts = [{"MatterAttachmentId": i, "MatterAttachmentName": f"A{i}", "MatterAttachmentHyperlink": f"https://x/{i}.pdf"}
            for i in range(1, 4)]
    judge = FakeJudge()
    import agenda_watcher.classify as c
    orig = c.extract_text
    c.extract_text = lambda url, http, **kw: "x" * 12_000
    try:
        classify.run_review(conn, FakeLegistar(attachments={2: atts}), judge, http())
    finally:
        c.extract_text = orig
    assert len(judge.review_calls[0]["attachment_text"]) <= 30_000


def test_review_retries_once_then_records_error_row(conn):
    setup(conn)
    judge = FakeJudge(review_failures=2)
    assert classify.run_review(conn, FakeLegistar(), judge, http()) == (1, 0)
    assert len(judge.review_calls) == 2
    (row,) = stage2(conn)
    assert row[1] is None and row[4].startswith("review failed")


def test_review_succeeds_on_the_retry(conn):
    setup(conn)
    assert classify.run_review(conn, FakeLegistar(), FakeJudge(review_failures=1), http()) == (1, 1)


def test_error_rows_are_retried_on_next_run(conn):
    setup(conn)
    classify.run_review(conn, FakeLegistar(), FakeJudge(review_failures=2), http())
    assert classify.run_review(conn, FakeLegistar(), FakeJudge(), http()) == (1, 1)
    assert stage2(conn)[0][1] == 1


def test_completed_reviews_are_not_repeated(conn):
    setup(conn)
    classify.run_review(conn, FakeLegistar(), FakeJudge(), http())
    judge = FakeJudge()
    assert classify.run_review(conn, FakeLegistar(), judge, http()) == (0, 0)
    assert judge.review_calls == []


def test_review_runs_when_all_attachments_fail(conn):
    setup(conn)
    att = {"MatterAttachmentId": 5, "MatterAttachmentName": "Scan", "MatterAttachmentHyperlink": "https://x/a.pdf"}
    judge = FakeJudge()
    assert classify.run_review(conn, FakeLegistar(attachments={2: [att]}), judge, http(status=404)) == (1, 1)
    assert judge.review_calls[0]["attachment_text"] == ""


def test_review_runs_when_attachment_listing_fails(conn):
    setup(conn)
    judge = FakeJudge()
    assert classify.run_review(conn, FakeLegistar(fail_attachments=True), judge, http()) == (1, 1)
    assert judge.review_calls[0]["attachment_names"] == []


def test_not_relevant_review_is_stored_and_not_counted_as_flagged(conn):
    setup(conn)
    judge = FakeJudge(review=Review(False, None, 0.8, "routine", "Minutes."))
    assert classify.run_review(conn, FakeLegistar(), judge, http()) == (1, 0)
    assert stage2(conn)[0][1] == 0


def test_unknown_category_from_model_is_stored_as_null_category():
    out = ReviewOut(relevant=True, category="pets", confidence=1.7, reason="r", summary="s")
    judge, messages = claude(SimpleNamespace(stop_reason="end_turn", parsed_output=out))
    review = judge.review("T", "Ordinance", "COUNCIL", "text", ["a"], "")
    assert review.relevant and review.category is None and review.confidence == 1.0
    assert messages.calls[0]["model"] == "review-m"
    assert "legally consider" in messages.calls[0]["system"]
