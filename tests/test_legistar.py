from datetime import date

import httpx
import pytest

from agenda_watcher.legistar import Legistar, LegistarError, odata_date


def make(handler):
    calls = []

    def wrapped(request):
        calls.append(request)
        return handler(request, len(calls))

    lg = Legistar("madison", http=httpx.Client(transport=httpx.MockTransport(wrapped)), pause=0, sleep=lambda s: None)
    return lg, calls


def test_paged_stops_on_short_page():
    sizes = {0: 1000, 1000: 1000, 2000: 12}

    def handler(request, n):
        skip = int(request.url.params["$skip"])
        return httpx.Response(200, json=[{"MatterId": skip + i} for i in range(sizes[skip])])

    lg, calls = make(handler)
    got = list(lg.paged("/matters", filter="X eq 1", orderby="MatterId"))
    assert len(got) == 2012
    assert [c.url.params["$skip"] for c in calls] == ["0", "1000", "2000"]
    assert calls[0].url.params["$top"] == "1000"
    assert calls[0].url.params["$filter"] == "X eq 1"
    assert calls[0].url.path == "/v1/madison/matters"


def test_message_object_raises_legistar_error():
    lg, _ = make(lambda r, n: httpx.Response(200, json={"Message": "bad"}))
    with pytest.raises(LegistarError, match="bad"):
        list(lg.paged("/matters"))


def test_http_400_raises_legistar_error_naming_url():
    lg, _ = make(lambda r, n: httpx.Response(400, json={"Message": "nope"}))
    with pytest.raises(LegistarError, match="/v1/madison/matters"):
        lg.get("/matters")


def test_retries_on_503_then_succeeds():
    lg, calls = make(lambda r, n: httpx.Response(503) if n < 3 else httpx.Response(200, json=[{"a": 1}]))
    assert lg.get("/events") == [{"a": 1}]
    assert len(calls) == 3


def test_gives_up_after_three_retries():
    lg, calls = make(lambda r, n: httpx.Response(503))
    with pytest.raises(LegistarError):
        lg.get("/events")
    assert len(calls) == 4


def test_retries_on_transport_error():
    def handler(request, n):
        if n == 1:
            raise httpx.ConnectError("down")
        return httpx.Response(200, json=[])

    lg, calls = make(handler)
    assert lg.get("/events") == []
    assert len(calls) == 2


def test_matter_text_uses_last_version():
    def handler(request, n):
        if request.url.path.endswith("/versions"):
            return httpx.Response(200, json=[{"Key": "5", "Value": "1"}, {"Key": "9", "Value": "2"}])
        return httpx.Response(200, json={"MatterTextPlain": "hello"})

    lg, calls = make(handler)
    assert lg.matter_text(3) == "hello"
    assert calls[-1].url.path == "/v1/madison/matters/3/texts/9"


def test_matter_text_empty_when_no_versions():
    lg, _ = make(lambda r, n: httpx.Response(200, json=[]))
    assert lg.matter_text(1) == ""


def test_matter_text_empty_when_plain_text_is_null():
    def handler(request, n):
        if request.url.path.endswith("/versions"):
            return httpx.Response(200, json=[{"Key": "5", "Value": "1"}])
        return httpx.Response(200, json={"MatterTextPlain": None})

    lg, _ = make(handler)
    assert lg.matter_text(1) == ""


def test_odata_date():
    assert odata_date(date(2025, 11, 1)) == "datetime'2025-11-01'"
