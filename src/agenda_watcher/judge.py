"""The LLM judgment, behind a small interface so everything else can be tested offline."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import anthropic
from pydantic import BaseModel, ValidationError

CATEGORIES = ("animal_agriculture", "wildlife_management", "captive_animals", "sale_and_use",
              "food_policy", "animal_services")

SCOPE = """\
You help animal advocates find local government decisions they may want to comment on.

An item is RELEVANT when a government body is deciding something that materially affects animals. Categories:
- animal_agriculture: slaughter and meat processing, livestock facilities, manure and water permits, \
agricultural zoning and conditional use permits
- wildlife_management: culls of deer, geese and other animals, trapping, pesticide and rodenticide use
- captive_animals: Henry Vilas Zoo (county-owned), circuses, rodeos, fairs, exhibitions
- sale_and_use: fur, foie gras, pet store sales, live markets
- food_policy: public procurement and food policy, institutional food contracts, plant-based defaults
- animal_services: animal services and animal control ordinances

NOT relevant: routine items that mention animals without a decision at stake that affects them, such as a \
dog park maintenance contract, a proclamation, meeting minutes, or an appointment to a board.

These decisions are often disguised in planning language. Look past the label for: a conditional use permit \
or rezoning for agricultural processing or meat processing, animal feeding operations, manure storage, \
nutrient management, wildlife "management" or "control" contracts, and zoo contracts."""

TRIAGE_SYSTEM = SCOPE + """

You will get a list of agenda items, one per line, as: id | type | body | title.
This is a first pass on titles only, and a closer review follows, so favour recall. When unsure, keep the item.
Return one decision for every id in the list: keep is true when the item is worth a closer look. \
Most lists contain none worth keeping."""

REVIEW_SYSTEM = SCOPE + """

You will get one agenda item with its text and any attached documents. Decide whether it is relevant.
Return:
- relevant: true or false
- category: one of the category names above, or null when not relevant
- confidence: 0 to 1, how sure you are of `relevant`
- reason: one sentence on why
- summary: two plain sentences saying what is being decided, for a reader who has not seen the item. \
Describe only what the documents say.

Do not say anything about what the body may legally consider, and do not suggest arguments."""


@dataclass(frozen=True)
class TriageItem:
    matter_id: int
    title: str
    type: str
    body_name: str


@dataclass(frozen=True)
class Review:
    relevant: bool
    category: str | None
    confidence: float
    reason: str
    summary: str


class JudgeError(Exception):
    """The model answered, but not usably (refusal, truncation, malformed output). About this input only."""


class JudgeDown(Exception):
    """The service could not be used. `fatal` means retrying cannot help (bad key, unknown model)."""

    def __init__(self, message: str, fatal: bool = False):
        super().__init__(message)
        self.fatal = fatal


class Judge(Protocol):
    def triage(self, items: list[TriageItem]) -> set[int]: ...

    def review(self, title: str, type: str, body_name: str, text: str,
               attachment_names: list[str], attachment_text: str) -> Review: ...


class TriageDecision(BaseModel):
    id: int
    keep: bool


class TriageOut(BaseModel):
    decisions: list[TriageDecision]


class ReviewOut(BaseModel):
    relevant: bool
    category: str | None
    confidence: float
    reason: str
    summary: str


def _oneline(s: str | None) -> str:
    return " ".join((s or "").split())


class ClaudeJudge:
    def __init__(self, triage_model: str, review_model: str, client=None):
        if client is None:
            client = anthropic.Anthropic()
        self.client = client
        self.triage_model, self.review_model = triage_model, review_model

    def _ask(self, model: str, system: str, content: str, output_format, max_tokens: int):
        try:
            resp = self.client.messages.parse(
                model=model, max_tokens=max_tokens, system=system,
                messages=[{"role": "user", "content": content}], output_format=output_format)
        except (anthropic.AuthenticationError, anthropic.PermissionDeniedError, anthropic.NotFoundError) as e:
            raise JudgeDown(f"{type(e).__name__}: {e}", fatal=True) from e
        except anthropic.APIError as e:  # rate limits, overload, network, and request errors such as no credit
            raise JudgeDown(f"{type(e).__name__}: {e}") from e
        except (ValidationError, ValueError) as e:  # the reply did not fit the schema
            raise JudgeError(f"{type(e).__name__}: {e}") from e
        if resp.stop_reason != "end_turn" or resp.parsed_output is None:
            raise JudgeError(f"incomplete response (stop_reason={resp.stop_reason})")
        return resp.parsed_output

    def triage(self, items: list[TriageItem]) -> set[int]:
        lines = [f"{i.matter_id} | {_oneline(i.type)} | {_oneline(i.body_name)} | {_oneline(i.title)}" for i in items]
        out = self._ask(self.triage_model, TRIAGE_SYSTEM, "\n".join(lines), TriageOut, 8000)
        ids = {i.matter_id for i in items}
        dropped = {d.id for d in out.decisions if not d.keep}
        return ids - dropped  # an id the model kept, or did not mention at all, goes on to review

    def review(self, title: str, type: str, body_name: str, text: str,
               attachment_names: list[str], attachment_text: str) -> Review:
        names = "\n".join(f"- {n}" for n in attachment_names) or "(none)"
        content = (f"Title: {_oneline(title)}\nType: {_oneline(type)}\nBody: {_oneline(body_name)}\n\n"
                   f"Item text:\n{text or '(none)'}\n\nAttachment names:\n{names}\n\n"
                   f"Text extracted from attachments:\n{attachment_text or '(none)'}")
        out = self._ask(self.review_model, REVIEW_SYSTEM, content, ReviewOut, 8000)
        category = out.category if out.category in CATEGORIES else None
        return Review(out.relevant, category if out.relevant else None,
                      min(1.0, max(0.0, out.confidence)), out.reason, out.summary)
