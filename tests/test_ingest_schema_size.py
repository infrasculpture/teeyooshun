"""The structured-output grammar has a size limit (a 400 "compiled grammar is too large").
These tests keep the ingestion schemas small and cover the non-strict fallback."""

import json
from types import SimpleNamespace

import anthropic
import httpx
import pytest

from app import llm
from app.ingest import extract, prompts
from ingest_helpers import FakeClient, a, page, q

# Ceilings in characters of json.dumps(schema), set from the sizes when this test was written.
# If one trips, shrink the schema; raise a budget only after checking the API still accepts it.
BUDGETS = {
    "exam paper, science": (prompts.schema_for("exam_paper_with_key", "science"), 5000),
    "exam paper, history": (prompts.schema_for("exam_paper_with_key", "history"), 3500),
    "answer key, science": (prompts.schema_for("answer_key", "science"), 3000),
    "answer key, history": (prompts.schema_for("answer_key", "history"), 2200),
}


@pytest.mark.parametrize("name", list(BUDGETS))
def test_schema_stays_under_its_size_budget(name):
    schema, budget = BUDGETS[name]
    size = len(json.dumps(schema))
    assert size <= budget, f"{name} schema is {size} characters, budget {budget}"


def test_answer_key_pages_only_ask_for_answers():
    props = prompts.schema_for("answer_key", "science")["properties"]
    assert "answers" in props and not {"questions", "sources", "section_marks"} & set(props)
    assert "unit" in props["answers"]["items"]["properties"]
    assert "unit" not in prompts.schema_for("exam_paper_with_key", "science")["properties"]["answers"]["items"]["properties"]


def test_expand_page_restores_the_stored_shape():
    reply = {"page_type": "content", "section_marks": ["A=1", "B = 2 marks"], "sources": [], "answers": [{"qnum": "1"}],
             "questions": [{"qnum": "1", "options": ["A. copper", "B) iron", "gold"], "figure": "weird",
                            "answer_format": "table"}]}
    data = prompts.expand_page(reply)
    assert data["section_marks"] == [{"section": "A", "marks_each": 1}, {"section": "B", "marks_each": 2}]
    question = data["questions"][0]
    assert question["options"] == [{"label": "A", "text": "copper"}, {"label": "B", "text": "iron"},
                                   {"label": "C", "text": "gold"}]
    assert question["is_mcq"] is True and question["figure"] == "none" and question["answer_format"] == "table"
    assert data["answers"][0]["unit"] == ""
    assert prompts.expand_page(json.loads(json.dumps(data))) == data  # safe to run twice


def test_expand_page_leaves_a_full_shape_page_alone():
    full = page(questions=[q("1", is_mcq=True, options=[("A", "x")])], answers=[a("1")], section_marks=[("A", 1)])
    assert prompts.expand_page(json.loads(json.dumps(full))) == full


def test_expand_page_adds_empty_lists_to_an_answer_key_page():
    data = prompts.expand_page({"page_type": "answer", "answers": []})
    assert data["questions"] == [] and data["sources"] == [] and data["section_marks"] == []


def test_check_against_schema_finds_problems():
    schema = prompts.schema_for("answer_key", "science")
    good = {"page_type": "blank", "page_type_reason": "", "rotation_needed": 0, "cover_has_other_content": False,
            "printed_page_number": "", "chapter_heading": "", "answers": [], "uncertain": False, "uncertain_reason": ""}
    assert llm.check_against_schema(good, schema) is None
    assert "missing answers" in llm.check_against_schema({k: v for k, v in good.items() if k != "answers"}, schema)
    assert "rotation_needed" in llm.check_against_schema({**good, "rotation_needed": 45}, schema)
    assert "page_type" in llm.check_against_schema({**good, "page_type": 3}, schema)


class _SchemaTooLarge(FakeClient):
    """Rejects any request that asks for strict output, like the API did."""

    def _create(self, **kwargs):
        if "format" in kwargs.get("output_config", {}):
            self.requests.append(kwargs)
            request = httpx.Request("POST", "https://example.invalid")
            raise anthropic.BadRequestError(
                "The compiled grammar is too large, which would cause performance issues.",
                response=httpx.Response(400, request=request), body=None)
        return super()._create(**kwargs)


SETTINGS = SimpleNamespace(ingest_model="claude-sonnet-5-5", ingest_effort="", refusal_fallback=False)


def _png(tmp_path):
    path = tmp_path / "p001.png"
    path.write_bytes(b"not really a png")
    return path


def test_too_large_schema_falls_back_to_non_strict_and_still_reads_the_page(conn, tmp_path, monkeypatch):
    monkeypatch.setattr(extract, "_NOT_STRICT", set())
    client = _SchemaTooLarge([page(questions=[q("1")], answers=[a("1")])] * 2)
    outcome = extract.process_page(conn, png=_png(tmp_path), material_type="exam_paper_with_key", subject="science",
                                   message="Page 1 of 1.", settings=SETTINGS, client=client)
    assert outcome.status == "done" and outcome.data["questions"][0]["qnum"] == "1"
    assert "format" in client.requests[0]["output_config"]  # first try was strict
    assert "output_config" not in client.requests[1]  # then without strict, schema in the system prompt
    assert "JSON schema" in client.requests[1]["system"][-1]["text"]

    again = extract.process_page(conn, png=_png(tmp_path), material_type="exam_paper_with_key", subject="science",
                                 message="Page 2 of 2.", settings=SETTINGS, client=client)
    assert again.status == "done" and len(client.requests) == 3  # the next page goes straight to non-strict


def test_non_strict_reply_with_the_wrong_shape_is_retried_once(conn, tmp_path, monkeypatch):
    monkeypatch.setattr(extract, "_NOT_STRICT", {"exam_paper_with_key"})
    client = FakeClient([{"page_type": "content"}, page(questions=[q("1")])])
    outcome = extract.process_page(conn, png=_png(tmp_path), material_type="exam_paper_with_key", subject="science",
                                   message="Page 1 of 1.", settings=SETTINGS, client=client)
    assert outcome.status == "done" and len(client.requests) == 2


def test_schema_error_message_does_not_blame_the_key(conn):
    client = _SchemaTooLarge([])
    with pytest.raises(llm.LLMError) as caught:
        llm.structured_call(conn, model="m", effort="", system=[], content=[], schema={"type": "object"},
                            purpose="ingest", fallback=False, client=client)
    assert caught.value.kind == "schema_error" and caught.value.fatal
    assert "check the API key" not in str(caught.value)
