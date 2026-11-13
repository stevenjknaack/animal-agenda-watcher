from pathlib import Path

import httpx

from agenda_watcher.attachments import extract_text

PDF = (Path(__file__).parent / "fixtures" / "small.pdf").read_bytes()


def http(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_extract_text_reads_fixture_pdf():
    text = extract_text("https://x/a.pdf", http(lambda r: httpx.Response(200, content=PDF)))
    assert "slaughterhouse at 123 Farm Road" in text


def test_extract_text_returns_empty_on_404():
    assert extract_text("https://x/a.pdf", http(lambda r: httpx.Response(404))) == ""


def test_extract_text_returns_empty_on_non_pdf_bytes():
    assert extract_text("https://x/a.pdf", http(lambda r: httpx.Response(200, content=b"<html>nope</html>"))) == ""


def test_extract_text_returns_empty_on_network_error():
    def boom(request):
        raise httpx.ConnectError("down")

    assert extract_text("https://x/a.pdf", http(boom)) == ""


def test_extract_text_stops_download_over_max_bytes():
    assert extract_text("https://x/a.pdf", http(lambda r: httpx.Response(200, content=PDF)), max_bytes=100) == ""


def test_extract_text_truncates_to_max_chars():
    text = extract_text("https://x/a.pdf", http(lambda r: httpx.Response(200, content=PDF)), max_chars=11)
    assert text == "Conditional"


def test_extract_text_returns_empty_for_missing_url():
    assert extract_text(None, http(lambda r: httpx.Response(200, content=PDF))) == ""
