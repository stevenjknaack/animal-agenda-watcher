"""Pull a little text out of a PDF attachment. Any failure means no text, never an error."""
from __future__ import annotations

import io
import logging

import httpx
from pypdf import PdfReader

logging.getLogger("pypdf").setLevel(logging.ERROR)


def extract_text(url: str | None, http: httpx.Client, max_bytes: int = 15_000_000, max_pages: int = 15,
                 max_chars: int = 12_000) -> str:
    if not url:
        return ""
    try:
        buf = io.BytesIO()
        with http.stream("GET", url, follow_redirects=True) as resp:
            if resp.status_code != 200:
                return ""
            for chunk in resp.iter_bytes():
                buf.write(chunk)
                if buf.tell() > max_bytes:
                    return ""
        buf.seek(0)
        parts, total = [], 0
        for page in PdfReader(buf).pages[:max_pages]:
            piece = page.extract_text() or ""
            parts.append(piece)
            total += len(piece)
            if total >= max_chars:
                break
        return "\n".join(parts).strip()[:max_chars]
    except Exception:
        return ""
