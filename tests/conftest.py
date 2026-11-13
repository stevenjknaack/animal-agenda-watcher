import pytest

from agenda_watcher import store


@pytest.fixture
def conn(tmp_path):
    c = store.connect(tmp_path / "t.db")
    yield c
    c.close()


def matter(matter_id=7, **over):
    m = {
        "MatterId": matter_id, "MatterFile": str(90000 + matter_id), "MatterTypeName": "Resolution",
        "MatterStatusName": "Passed", "MatterTitle": f"Title {matter_id}", "MatterName": None,
        "MatterBodyName": "COMMON COUNCIL", "MatterIntroDate": "2025-03-04T00:00:00",
        "MatterLastModifiedUtc": "2025-03-05T10:00:00.1",
    }
    m.update(over)
    return m


def event(event_id=1, date="2025-03-12", status="Approved", **over):
    e = {
        "EventId": event_id, "EventBodyName": "PLAN COMMISSION", "EventDate": f"{date}T00:00:00",
        "EventTime": "5:30 PM", "EventLocation": "Room 201", "EventAgendaFile": f"https://x/{event_id}.pdf",
        "EventInSiteURL": f"https://x/meeting/{event_id}", "EventAgendaStatusName": status,
    }
    e.update(over)
    return e


def item(item_id, matter_id=None, title="Line"):
    return {"EventItemId": item_id, "EventItemMatterId": matter_id, "EventItemTitle": title}
