# Marking Spec (v1)

*How Teeyooshun marks an answer. For Claude Code to implement. Examples here are made up; real questions and answers live only in the server's data directory.*

## 1. Principles

1. **Claude reads the answer; code keeps the score.** The model reports which mark points (or which level features) the answer meets, quoting the student's own words as evidence. Code adds up the marks, applies caps and rules, and decides confidence. The model never states a total that code doesn't check.
2. **Every awarded mark needs a verbatim quote.** Code checks that each `evidence_quote` really appears in the student's answer (case- and whitespace-insensitive). If it doesn't, that mark is **not** awarded and the attempt is flagged `evidence_mismatch`. This stops invented credit.
3. **Strict on terminology, like school marking.** A mark point needs its key idea in correct scientific terms. Vague wording ("same water" for "volume of water") does not earn the mark. Accepted alternatives come only from the stored mark scheme.
4. **Wrong way round = no mark.** If the answer states the opposite of the mark point (open instead of close, higher instead of lower), the point is not awarded and a `reversed` flag is set.
5. **Conservative when unsure.** Borderline → award the lower mark or level, set `borderline: true`, give a one-line reason.
6. **Score and technique are separate.** The score follows the mark scheme only. Technique feedback (filler, length, command word) never changes the score.

## 2. Question record

Stored in the database; imported from JSON files in the data directory.

| Field | Meaning |
|---|---|
| `id` | Stable ID, e.g. `sci-s1-mcass-c1-q2f` |
| `subject`, `level`, `topic` | e.g. `science`, `sec1`, `sci-01-scientific-endeavour` (topic IDs from the topic map) |
| `source` | `assessment_book` / `publisher_paper` / `school` / `generated` |
| `source_ref` | Book, page, question number (for "Book 2 p.32 Q5" entry) |
| `context` | Shared stem text, plus a written description of any figure or table |
| `question` | The question itself |
| `marks` | Maximum marks |
| `command_word` | state / name / describe / explain / suggest / compare / infer … (from the syllabus glossary) |
| `marking_mode` | `points` or `lorms` |
| `mark_points` | For `points` (see below) |
| `choose` | Optional: "any N" of the mark points count (defaults to `marks`) |
| `lorms` | For `lorms`: levels with mark ranges, descriptors, award rules, exemplars |
| `rejects` | Optional: answers the scheme explicitly rejects (e.g. "explode") |

Mark point: `{ "id": "MP1", "text": "...", "key_terms": ["..."], "alternatives": ["..."], "requires": ["MP0"] }`. `requires` means this point only counts if the listed point was awarded (e.g. an explanation only counts if the choice before it was right).

## 3. Marking call (points mode)

**Input to the model:** system prompt (cached) → question record without internal IDs (cached per question) → the student's answer (last, uncached).

**Output (strict JSON via the API's structured-output support):**

```json
{
  "points": [
    {"id": "MP1", "awarded": true, "evidence_quote": "exact words from answer", "reason": "one line"},
    {"id": "MP2", "awarded": false, "evidence_quote": "", "reason": "one line", "reversed": false}
  ],
  "missing_key_terms": ["..."],
  "filler_quotes": ["exact words that earned nothing"],
  "misread_question": false,
  "borderline": false,
  "borderline_reason": "",
  "full_marks_answer": "a model answer, as short as the marks allow",
  "technique_note": "one or two sentences: length vs marks, command word, structure"
}
```

**Code then:** verifies quotes → applies `requires` → counts awarded points → caps at `choose`/`marks` → computes confidence → stores the attempt → renders feedback.

## 4. Marking call (LORMS mode)

The model reports features and a proposed level; code validates.

```json
{
  "features": [
    {"claim": "exact words stating the inference or point", "supported": true,
     "support_quote": "exact words citing the source or example", "explained": true}
  ],
  "describes_without_inference": false,
  "proposed_level": 3,
  "proposed_marks": 4,
  "descriptor_matched": "text of the level descriptor it meets",
  "why_not_higher": "one line",
  "borderline": true,
  "borderline_reason": "one line",
  "peel_map": {"point": "...", "evidence": "...", "explain": "...", "link": "... or MISSING"},
  "full_marks_answer": "...",
  "technique_note": "..."
}
```

**Code then:** verifies quotes → checks `proposed_marks` sits inside the level's range (else clamp and flag) → if the level has countable award rules (e.g. "4 = one supported inference, 5 = two"), recomputes marks from `features` and uses the lower of the two → confidence → store → render.

## 5. Confidence (computed by code)

| Mark-scheme source | Base |
|---|---|
| `assessment_book` | High |
| `school`, `publisher_paper` | Medium |
| `generated` | Low |

Drop one step if `borderline`, `evidence_mismatch`, a clamp happened, or `marking_mode` is `lorms`. Shown to the student as a simple label; shown to the parent with the reason.

## 6. Feedback shown to the student (rendered by code, not free text)

1. Score and confidence.
2. **What earned marks:** her quotes, highlighted, with the mark point each earned.
3. **What didn't earn marks and why:** unawarded points, reversed statements, filler quotes.
4. **Key terms to use:** `missing_key_terms`.
5. **What full marks needs:** `full_marks_answer`.
6. **Technique:** `technique_note`.
7. If marks were lost on a multi-mark question: **rewrite prompt** (with skip), re-marked with the same call; first-attempt marks are what count in stats.

Follow-up questions ("why is that wrong?") are a separate, small chat call with the question, her answer and the marking JSON as context.

## 7. Models and cost

- Model names come from `.env`: `MARKING_MODEL` (multi-mark and LORMS), `LIGHT_MODEL` (hints, 1-mark, follow-ups). Lowest temperature the model allows.
- Use prompt caching on the system prompt and question record.
- Log input/output tokens per call so the parent dashboard can show monthly API cost.

## 8. System prompt (points mode, draft)

> You are marking a Singapore Secondary G3 Science answer against the mark scheme provided. Mark like a strict but fair school examiner. Award a mark point only if the answer contains its key idea in correct scientific terms, or one of the listed alternatives. Do not credit vague wording, restatements of the question, or ideas not in the scheme. If the answer states the opposite of a mark point, do not award it and set "reversed". For every awarded point, copy the exact words from the answer that earn it into evidence_quote; if you cannot quote it, do not award it. List any words that earned nothing as filler_quotes. If unsure, do not award the point and set borderline with a reason. Write full_marks_answer as the shortest answer that would earn full marks: about one sentence per mark. Write technique_note in plain, encouraging language for a 13-year-old, focused on one thing she can do differently. Return only the JSON.

## 9. System prompt (LORMS mode, draft)

> You are marking a Singapore Lower Secondary History or Geography answer using the level of response marking scheme (LORMS) provided. First identify each point or inference the answer makes and whether it is supported by specific evidence from the source or a specific example, and whether it is explained. Retelling or copying the source is not an inference. Then choose the highest level whose descriptor the answer fully meets and a mark within that level's range, following the scheme's award rules exactly. If the answer is between levels, choose the lower level and set borderline. Quote the answer's exact words for every claim and support. Map the answer onto PEEL (point, evidence, explain, link), writing MISSING for absent parts. Write full_marks_answer as a concise PEEL answer using the student's own points where possible. Return only the JSON.

## 10. Calibration

- A calibration file (`data/calibration/*.json`, not in git) holds real questions with test answers and expected marks, including tolerance.
- A command marks every case and prints a table: expected, awarded, difference, confidence, flags; plus totals.
- Targets for v1: points mode exact on at least 80% of cases and never more than 1 mark off; LORMS within one level on every case and within 1 mark on at least 80%.
- Re-run calibration whenever the prompt or model changes.
