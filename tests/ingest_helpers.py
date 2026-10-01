"""Helpers for ingestion tests: made-up PDFs drawn with Pillow, and a fake API
client returning canned page JSON. No real sources, no API calls."""

import json
from pathlib import Path
from types import SimpleNamespace

from PIL import Image, ImageDraw

SOURCE_YAML = """title: Test Book
subject: science
level: sec1
files:
  "qns_*": questions
  "answers.pdf": answer_key
key_reliability: assessment_book
units:
  "01": sci-01-scientific-endeavour
"""

PAPERS_YAML = """title: Test Papers
subject: science
level: sec1
files:
  "*.pdf": exam_paper_with_key
key_reliability: publisher_paper
"""


def make_pdf(path, pages: list, upside_down=()) -> Path:
    """A PDF with one A4-shaped page per text, each with a black square near the top left."""
    images = []
    for i, text in enumerate(pages, 1):
        im = Image.new("RGB", (1240, 1754), "white")
        draw = ImageDraw.Draw(im)
        draw.text((100, 100), text, fill="black")
        draw.rectangle((100, 200, 300, 400), fill="black")
        if i in upside_down:
            im = im.rotate(180)
        images.append(im)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    images[0].save(path, save_all=True, append_images=images[1:], resolution=150)
    return path


def make_source(root, slug: str, yaml_text: str, pdfs: dict) -> Path:
    """data/sources/science/sec1/<slug>/ with source.yaml and the given PDFs {name: [page texts]}."""
    folder = Path(root) / "sources" / "science" / "sec1" / slug
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "source.yaml").write_text(yaml_text, encoding="utf-8")
    for name, pages in pdfs.items():
        make_pdf(folder / name, pages)
    return folder


# ---- Canned model output -------------------------------------------------------------

def page(page_type="content", questions=(), answers=(), rotation=0, chapter="", printed="1",
         cover_other=False, uncertain=False, reason="", type_reason=""):
    return {"page_type": page_type, "page_type_reason": type_reason,
            "rotation_needed": rotation, "cover_has_other_content": cover_other,
            "printed_page_number": printed, "chapter_heading": chapter, "questions": list(questions),
            "answers": list(answers), "uncertain": uncertain, "uncertain_reason": reason}


def q(qnum, part="", question="Explain what happens.", marks=2, cont=False, context="", figure="none",
      is_mcq=False, options=(), topic="", uncertain=False, reason=""):
    return {"qnum": qnum, "part": part, "continues_from_previous_page": cont, "context": context,
            "question": question, "marks": marks, "command_word": "explain", "figure": figure,
            "is_mcq": is_mcq, "options": [{"label": l, "text": t} for l, t in options], "topic": topic,
            "uncertain": uncertain, "uncertain_reason": reason}


def a(qnum, part="", unit="", points=("First point", "Second point"), mode="points", cont=False, choose=0,
      lorms=(), correct="", requires=None, key_terms=None):
    mps = [{"text": t, "key_terms": list(key_terms or []), "alternatives": [], "requires": []} for t in points]
    if requires:
        for index, needs in requires.items():
            mps[index - 1]["requires"] = needs
    return {"unit": unit, "qnum": qnum, "part": part, "continues_from_previous_page": cont, "mode": mode,
            "mark_points": mps if mode == "points" else [], "choose": choose, "rejects": [],
            "lorms": list(lorms), "correct_option": correct, "uncertain": False, "uncertain_reason": ""}


USAGE = {"input_tokens": 2000, "output_tokens": 500, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 100}


class FakeClient:
    """Stands in for anthropic.Anthropic(). Each call returns the next canned page.

    "TRUNCATED" in the list simulates a reply cut off at max_tokens (a non-fatal failure).
    Running out of canned pages fails the test, so unexpected calls are caught.
    """

    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []
        self.messages = SimpleNamespace(create=self._create)
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.requests.append(kwargs)
        if not self.responses:
            raise AssertionError("unexpected API call")
        r = self.responses.pop(0)
        if r == "TRUNCATED":
            return self._response("", "max_tokens")
        return self._response(json.dumps(r), "end_turn")

    @staticmethod
    def _response(text, stop_reason):
        return SimpleNamespace(
            stop_reason=stop_reason, model="claude-sonnet-5-5", _request_id="req_test",
            content=[SimpleNamespace(type="text", text=text)], usage=SimpleNamespace(**USAGE))

    def messages_text(self, i: int) -> str:
        """The text part of request i's user message."""
        return self.requests[i]["messages"][0]["content"][1]["text"]


# ---- Running the ingest command with a fake client ----------------------------------------

def ingest(folder, responses, **kwargs):
    """Run the ingest command. Returns (exit code, fake client, printed lines)."""
    from app.ingest.__main__ import run
    client, lines = FakeClient(responses), []
    code = run(folder, client=client, out=lines.append, **kwargs)
    return code, client, lines


# A made-up assessment book: chapter 1 questions plus the answer section.
BOOK_PAGES = {
    # answers.pdf sorts first, so it is processed first
    "answers.pdf": [
        page("answer", chapter="1", answers=[a("1", "a"), a("1", "b")]),
        page("answer", answers=[a("2"), a("9")]),  # no heading: still chapter 1; Q9 has no question
    ],
    "qns_c01.pdf": [
        page("cover", type_reason="name box"),
        page(questions=[q("1", "a"), q("1", "b", question="Explain why")], printed="5"),
        page(questions=[q("", "", question="the sky is blue.", cont=True), q("2", question="Name it.", marks=1)],
             printed="6"),
    ],
}


def book(data_dir):
    return make_source(data_dir, "test-book", SOURCE_YAML, {"answers.pdf": ["Answers 1", "Answers 2"],
                                                           "qns_c01.pdf": ["Name: ____", "Q1", "Q1 cont, Q2"]})


def all_responses():
    return BOOK_PAGES["answers.pdf"] + BOOK_PAGES["qns_c01.pdf"]
