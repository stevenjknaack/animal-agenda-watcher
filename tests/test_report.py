from datetime import date
from pathlib import Path

from agenda_watcher import classify, config, evalset, report, store
from conftest import event, item, matter
from fakes import FakeJudge, FakeLegistar

CFG = config.load(Path(__file__).resolve().parents[1] / "config.toml")
TODAY = date(2026, 11, 12)


def seed(conn, flagged=(1,), n=3, intro="2025-03-01"):
    for i in range(1, n + 1):
        store.upsert_matter(conn, "madison", matter(i, MatterIntroDate=f"{intro}T00:00:00"))
    classify.run_triage(conn, FakeJudge(keep=set(flagged)))
    classify.run_review(conn, FakeLegistar(), FakeJudge(), None)


def on_agenda(conn, event_id, matter_id, date_, status="Approved"):
    store.upsert_event(conn, "madison", event(event_id, date=date_, status=status))
    store.upsert_event_item(conn, "madison", event_id, item(event_id * 100 + matter_id, matter_id))


def test_notice_days_uses_first_non_cancelled_meeting(conn):
    seed(conn)
    on_agenda(conn, 1, 1, "2025-03-05", status="Cancelled")
    on_agenda(conn, 2, 1, "2025-03-12")
    on_agenda(conn, 3, 1, "2025-04-02")
    assert report.notice_days(conn, "madison", 1) == 11


def test_notice_days_none_when_never_on_agenda(conn):
    seed(conn)
    assert report.notice_days(conn, "madison", 1) is None


def test_notice_days_ignores_meetings_before_introduction(conn):
    seed(conn)
    on_agenda(conn, 1, 1, "2025-02-20")
    on_agenda(conn, 2, 1, "2025-03-04")
    assert report.notice_days(conn, "madison", 1) == 3


def test_human_label_overrides_model_verdict_in_counts(conn):
    seed(conn, flagged=(1, 2))
    evalset.record_label(conn, "madison", 1, False, None, "flagged")
    evalset.record_label(conn, "madison", 3, True, "wildlife_management", "keyword")
    md = report.build(conn, CFG, today=TODAY)
    assert "**2 items**" in md
    assert "wildlife_management | 1" in md and "animal_agriculture | 1" in md


def test_report_states_number_hand_checked(conn):
    seed(conn, flagged=(1, 2))
    evalset.record_label(conn, "madison", 1, True, None, "flagged")
    evalset.record_label(conn, "madison", 2, False, None, "flagged")
    evalset.record_label(conn, "madison", 3, False, None, "random")
    md = report.build(conn, CFG, today=TODAY)
    assert "3 items were hand-checked" in md and "1 of 2" in md


def test_report_says_accuracy_unknown_when_nothing_hand_checked(conn):
    seed(conn)
    assert "accuracy is unknown" in report.build(conn, CFG, today=TODAY)


def test_report_with_zero_flagged_items_renders(conn):
    seed(conn, flagged=())
    md = report.build(conn, CFG, today=TODAY)
    assert "**0 items**" in md and "No notice figures" in md


def test_report_on_empty_database_renders(conn):
    assert "**0 items**" in report.build(conn, CFG, today=TODAY)


def test_report_warns_when_classification_is_incomplete(conn):
    seed(conn)
    store.upsert_matter(conn, "madison", matter(50))
    assert "1 items have not been classified" in report.build(conn, CFG, today=TODAY)


def test_report_counts_agenda_lines_without_a_matter(conn):
    seed(conn)
    store.upsert_event(conn, "madison", event(9))
    store.upsert_event_item(conn, "madison", 9, item(901, None, "Discussion of the rodeo"))
    store.upsert_event_item(conn, "madison", 9, item(902, None, "Call to order"))
    md = report.build(conn, CFG, today=TODAY)
    assert "2 agenda lines" in md and "1 of them" in md


def test_report_lists_flagged_items_with_summary_link_and_notice(conn):
    seed(conn)
    on_agenda(conn, 2, 1, "2025-03-12")
    md = report.build(conn, CFG, today=TODAY)
    assert "Title 1" in md and "A summary." in md and store.matter_url("madison", 1) in md
    assert "median 11 days" in md
    assert "legally" not in md
