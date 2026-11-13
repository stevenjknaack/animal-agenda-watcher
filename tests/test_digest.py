from datetime import date, datetime
from pathlib import Path

import pytest

from agenda_watcher import classify, config, digest, evalset, store
from conftest import event, item, matter
from fakes import FakeJudge, FakeLegistar

CFG = config.load(Path(__file__).resolve().parents[1] / "config.toml")
TODAY = date(2026, 11, 16)
ENV = {"SMTP_HOST": "smtp.example", "SMTP_PORT": "587", "SMTP_USER": "u", "SMTP_PASSWORD": "p", "DIGEST_TO": "me@example"}


def seed(conn, flagged=(1,), n=2, intro="2026-01-05"):
    for i in range(1, n + 1):
        store.upsert_matter(conn, "madison", matter(i, MatterIntroDate=f"{intro}T00:00:00"))
    classify.run_triage(conn, FakeJudge(keep=set(flagged)))
    classify.run_review(conn, FakeLegistar(), FakeJudge(), None)


def on_agenda(conn, event_id, matter_id, date_, status="Approved"):
    store.upsert_event(conn, "madison", event(event_id, date=date_, status=status))
    store.upsert_event_item(conn, "madison", event_id, item(event_id * 100 + matter_id, matter_id))


def pending(conn):
    return digest.pending(conn, CFG, TODAY)


def test_pending_excludes_cancelled_past_and_far_future_meetings(conn):
    seed(conn)
    on_agenda(conn, 1, 1, "2026-11-19")
    on_agenda(conn, 2, 1, "2026-11-21", status="Cancelled")
    on_agenda(conn, 3, 1, "2050-09-24")
    on_agenda(conn, 4, 1, "2026-11-10")
    assert [(e.matter_id, e.event_id, e.meeting_date) for e in pending(conn)] == [(1, 1, date(2026, 11, 19))]


def test_pending_includes_meeting_today(conn):
    seed(conn)
    on_agenda(conn, 1, 1, "2026-11-16")
    assert len(pending(conn)) == 1


def test_pending_excludes_unflagged_matters(conn):
    seed(conn)
    on_agenda(conn, 1, 2, "2026-11-19")
    assert pending(conn) == []


def test_human_label_decides_what_is_pending(conn):
    seed(conn, flagged=(1,))
    on_agenda(conn, 1, 1, "2026-11-19")
    on_agenda(conn, 1, 2, "2026-11-19")
    evalset.record_label(conn, "madison", 1, False, None, "flagged")
    evalset.record_label(conn, "madison", 2, True, "captive_animals", "keyword")
    assert [(e.matter_id, e.category) for e in pending(conn)] == [(2, "captive_animals")]


def test_pending_excludes_already_digested_pairs(conn):
    seed(conn)
    on_agenda(conn, 1, 1, "2026-11-19")
    digest.mark_sent(conn, pending(conn), datetime(2026, 11, 16, 12))
    assert pending(conn) == []


def test_same_matter_on_a_new_meeting_is_pending_again(conn):
    seed(conn)
    on_agenda(conn, 1, 1, "2026-11-19")
    digest.mark_sent(conn, pending(conn), datetime(2026, 11, 16, 12))
    on_agenda(conn, 2, 1, "2026-12-03")
    assert [e.event_id for e in pending(conn)] == [2]


def test_recent_flagged_matter_without_meeting_is_included_once(conn):
    seed(conn, intro="2026-11-10")
    (entry,) = pending(conn)
    assert (entry.event_id, entry.meeting_date) == (0, None)
    digest.mark_sent(conn, [entry], datetime(2026, 11, 16, 12))
    assert pending(conn) == []


def test_old_flagged_matter_without_meeting_is_not_included(conn):
    seed(conn, intro="2026-10-01")
    assert pending(conn) == []


def test_entry_carries_how_to_comment_from_config(conn):
    seed(conn)
    on_agenda(conn, 1, 1, "2026-11-19")
    (e,) = pending(conn)
    assert e.how_to_comment == CFG.client("madison").how_to_comment
    assert e.comment_source_url == CFG.client("madison").comment_source_url
    assert e.link == store.matter_url("madison", 1) and e.summary == "A summary."


def test_render_empty_says_nothing_new():
    subject, body = digest.render([], TODAY)
    assert subject == "Animal agenda watch: nothing new this week"
    assert "ran" in body


def test_render_orders_by_meeting_date_and_includes_comment_source(conn):
    seed(conn, flagged=(1, 2))
    on_agenda(conn, 1, 1, "2026-12-03")
    on_agenda(conn, 2, 2, "2026-11-19")
    subject, body = digest.render(pending(conn), TODAY)
    assert subject == "Animal agenda watch: 2 items"
    assert body.index("Title 2") < body.index("Title 1")
    assert CFG.client("madison").comment_source_url in body and "Thursday 19 November 2026" in body
    assert "legally" not in body


def test_render_singular_subject(conn):
    seed(conn)
    on_agenda(conn, 1, 1, "2026-11-19")
    assert digest.render(pending(conn), TODAY)[0] == "Animal agenda watch: 1 item"


class FakeSMTP:
    sent = []
    fail = False

    def __init__(self, host, port):
        self.host, self.port = host, port

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def starttls(self):
        pass

    def login(self, user, password):
        self.user = user

    def send_message(self, msg):
        if FakeSMTP.fail:
            raise OSError("smtp down")
        FakeSMTP.sent.append(msg)


@pytest.fixture
def smtp():
    FakeSMTP.sent, FakeSMTP.fail = [], False
    return FakeSMTP


def test_send_builds_message_from_env(smtp):
    digest.send("Subj", "Body", ENV, smtp_class=smtp)
    (msg,) = smtp.sent
    assert msg["Subject"] == "Subj" and msg["To"] == "me@example" and msg["From"] == "u"
    assert msg.get_content().strip() == "Body"


def test_send_names_missing_settings(smtp):
    with pytest.raises(digest.DigestError, match="SMTP_PASSWORD"):
        digest.send("S", "B", {k: v for k, v in ENV.items() if k != "SMTP_PASSWORD"}, smtp_class=smtp)


def test_run_sends_and_marks_sent(conn, smtp):
    seed(conn)
    on_agenda(conn, 1, 1, "2026-11-19")
    assert digest.run(conn, CFG, TODAY, ENV, smtp_class=smtp) == 1
    assert len(smtp.sent) == 1 and pending(conn) == []


def test_second_run_sends_nothing_new(conn, smtp):
    seed(conn)
    on_agenda(conn, 1, 1, "2026-11-19")
    digest.run(conn, CFG, TODAY, ENV, smtp_class=smtp)
    digest.run(conn, CFG, TODAY, ENV, smtp_class=smtp)
    assert [m["Subject"] for m in smtp.sent] == ["Animal agenda watch: 1 item", "Animal agenda watch: nothing new this week"]


def test_dry_run_does_not_send_or_mark_sent(conn, smtp):
    seed(conn)
    on_agenda(conn, 1, 1, "2026-11-19")
    printed = []
    digest.run(conn, CFG, TODAY, ENV, dry_run=True, out=printed.append, smtp_class=smtp)
    assert smtp.sent == [] and len(pending(conn)) == 1 and "Title 1" in printed[0]


def test_failed_send_does_not_mark_sent(conn, smtp):
    seed(conn)
    on_agenda(conn, 1, 1, "2026-11-19")
    smtp.fail = True
    with pytest.raises(digest.DigestError):
        digest.run(conn, CFG, TODAY, ENV, smtp_class=smtp)
    assert len(pending(conn)) == 1
