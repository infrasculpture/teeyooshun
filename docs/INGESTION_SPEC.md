# Ingestion Spec (v1)

*How scanned PDFs become question-bank records and revision cards. For Claude Code to implement as Task 4. Real source files live only in the server data directory; nothing copyrighted goes into git. Examples in this spec are made up.*

## 1. Scope of v1

| Material type (`source.yaml` value) | Becomes | In v1? |
|---|---|---|
| `questions` (assessment-book question files) | Question records (points or LORMS) | Yes |
| `answer_key` (assessment-book answer section) | Mark schemes attached to those questions | Yes |
| `exam_paper_with_key` (one paper plus its key in one PDF) | Question records with mark schemes | Yes |
| `revision_tables` | Revision cards (stored now; Revise mode UI is Task 5) | Yes |
| `teaching_text`, `school_notes`, `worksheets` | Notes library | **No** (later task) |

## 2. Folder layout and naming

```
data/sources/<subject>/<level>/<source-slug>/
  source.yaml
  qns_c01.pdf  qns_c02.pdf …    questions, one file per chapter (or qns_u01 per unit)
  answers.pdf                    the book's whole answer section
  rev_u01.pdf  rev_u02.pdf …     revision tables, one file per unit
  2024_jwss_eoy.pdf …            exam papers (in a papers/ folder), paper + key in one file
```

- Two-digit numbers; optional letter suffix for split files (`qns_c05a.pdf`).
- The `source.yaml` `files` map assigns a material type to each filename pattern (glob).

## 3. `source.yaml`

```yaml
title: Example Assessment Book Sec 1        # human name, used in source_ref
publisher: Example Publishing
subject: science                            # science | history | geography
level: sec1                                 # sec1..sec4
files:                                      # glob -> material type (first match wins)
  "qns_*": questions
  "answers.pdf": answer_key
  "rev_*": revision_tables
key_reliability: assessment_book            # assessment_book | publisher_paper | school
units:                                      # file number -> topic id from the topic map
  "01": sci-01-scientific-endeavour
  "02": sci-02-physical-properties
# For a papers/ folder instead:
# files: { "*.pdf": exam_paper_with_key }
# key_reliability: publisher_paper
# (no units: the model assigns a topic per question from the subject/level topic list)
```

Optional `numbering_overrides` maps, per file, a question number in that file to the number its answer has in the key, for cases automatic alignment can't resolve: `numbering_overrides: {"2024_jwss_eoy.pdf": {"C12": "C1", "C13": "C2"}}`. Overrides take priority over automatic alignment.

Validation: unknown keys are warnings; a missing `subject`, `level` or `files` is an error; a numbered file with no `units` entry is a warning (items get `topic: unassigned` and are flagged on the review page).

## 4. Pipeline

Command: `docker compose run --rm app python -m app.ingest data/sources/<subject>/<level>/<slug>` (optional `--file rev_u01.pdf`, `--dry-run`).

1. **Register.** Hash each PDF; skip files already processed with the same hash (re-running is safe). Changed file = new version, old staged items marked superseded.
2. **Render.** Render each page to PNG, then downscale so the long edge is at most the model's native limit (1568 px for most models; configurable). Keep renders in the data directory for the review page.
3. **Orientation.** Detect upside-down or sideways pages and rotate them before extraction (model-reported orientation, or a cheap check); record the rotation.
4. **Classify page.** Per page: `cover` (skip; never extract names), `content`, `answer`, `blank`, `other`. Pages with a name/class/index box are cover pages.
5. **Extract.** Send each content page image with a prompt specific to the file's material type (section 5). Returns strict JSON. Model: `INGEST_MODEL` from `.env` (default the marking model). Prompt-cache the per-type system prompt.
6. **Stitch.** Join items that continue across pages (an answer with no prompt at the top of a page belongs to the last item of the previous page). Drop exact duplicates from shared boundary pages.
7. **Match answers.** For `questions` + `answer_key`: match by chapter/unit number + question number + part (e.g. `c01 / 2 / f`). For `exam_paper_with_key`: the key section of the same file. Question numbers keep their section letter (`B5`), including when a question continues onto a new page. Where a section is numbered differently in the paper and the key (paper `C12, C13`; key `C1, C2`), the section is matched in order, but only when both have the same number of questions with the same part structure; each such item gets a "matched by position: confirm" warning and the mapping is listed in the summary. `numbering_overrides` in `source.yaml` take priority. Unmatched questions and orphan answers are flagged. The numbering check runs within each section.
   MCQ questions with no printed marks take the section's stated marks per question (from the instructions page), else 1, with no warning.
8. **Validate** (section 6).
9. **Stage.** Write items to `staged_items` with status `pending`, their page references, warnings and the raw model JSON. Nothing reaches the live question bank or card deck until approved.
10. **Log.** Tokens and cost per file, pages processed, items staged, warnings.

## 5. What extraction returns

**Questions / exam papers** (one item per question part, same shape as the calibration question record, minus `answers`):
`id` (generated: `<subject>-<level>-<slug>-<file>-q<num><part>`), `source_ref` (title + printed page number + question number), `context` (shared stem text plus a written description of any figure/table), `question`, `marks` (from the bracketed number), `command_word`, `figure` (`none` | `described` | `needed_to_answer`), `mark_points` or `lorms` from the key, `choose`, `requires`, `rejects`, `topic`.
- MCQ questions: `marking_mode: mcq` with `options` and `correct_option`; code marks these without the API.
- `figure: needed_to_answer` means the question depends on a figure, graph or table: the page image is shown to the student and sent with the marking call. It stays in typed practice.
- `answer_format`: `text` | `table` | `draw_or_label`. Only `draw_or_label` (the student must draw, sketch, plot or label on a diagram) is excluded from typed practice. Table-completion questions are typed.
- Worked examples (`worked_example: true`) are excluded from practice, because their page shows the solution.
- Page images: each question page also gets a sharper display render. A question record lists the pages holding any part of that question number (`page_images`). Only pages classed as question pages are listed or shown; answer pages, and question pages that also carry solutions, are never shown to the student.
- Bold words in keys are captured as `key_terms`.

**Revision tables** (one card per table row), matching the card shape in `docs/card_format_example.json`:
`id`, `section` (the heading above the table), `source_ref`, `command_word` (from the left column), `prompt`, `answer` with `format` one of `bullets` | `list` | `table` | `grouped` | `timeline` | `image`, points with `key_terms` (bold words) and `sub` points, optional `examples` ("For example" blocks), optional `notes` ("Remark" boxes), `recall` (`typed_or_paper`, or `self_tick_only` for maps/images), `topic`.
- Answer shapes (all in `docs/card_format_example.json`): `bullets` and `list` use `answer.points` (a list is shown numbered); `grouped` uses `answer.groups` of `{heading, points}`; `table` uses `answer.table = {headers, rows}` plus a flat `answer.key_terms`; `timeline` uses `answer.events` of `{date, text, key_terms}`; `image` uses `answer.image` and `answer.alt`.
- Images: store a reference to the page's display render plus a bounding box if the model gives one, and a written `alt` description. The box is `[left, top, right, bottom]` in parts per thousand of the page width and height, so it fits any render size. Accurate cropping is a later improvement; v1 may show the whole page region.
- Answers longer than about 8 points: split into sub-cards along their own structure (one per group, or one per top-level point when points have sub-points), flagged for review. A long flat list has no structure to split on: it is kept whole with a warning, never cut at an arbitrary point.
- A page of revision content with no question column (e.g. "Timeline of important events": dates and events) is content, never "other": it becomes one card with the page heading as its prompt, in the matching format (timeline for date and event pairs, list or bullets otherwise).
- A row that continues onto the next page joins its card; a row with no heading takes the last table heading seen. A card with an empty prompt at the top of a page is the rest of the last card on the previous page and is merged into it, table rows into the same table. It stays separate only if the model says both that the previous page's last card was complete and that this one is not a continuation.
- Card IDs are stable: `<subject>-<level>-<slug>-<file>-p<page>-<hash8>`, where `hash8` is the first 8 characters of a hash of the normalised prompt. Sub-cards add `a`, `b`, … The ID is given when the card is first staged and never recomputed: editing the prompt keeps it, and on a re-read a row whose normalised prompt matches an existing card from that file (as first read or as edited) reuses that card's ID. A prompt that is close to an existing card's but not the same gets a new ID and a warning.
- Each page also reports how many table rows it has, so the summary can account for every row (rows counted, entries returned, cards after joining, cards after splitting).
- Approved cards go to `revision_cards` with a version history.

## 6. Validation (warnings shown on the review page)

- Question without marks; question without a matched mark scheme; mark points fewer than marks with no `choose`.
- LORMS levels overlapping or leaving gaps (reuse the existing importer check).
- Card without a prompt or without any answer point.
- Duplicate IDs; `topic: unassigned`; drawing or labelling questions; worked examples; a figure-dependent question with no page image to show.
- Obvious OCR/extraction oddities the model reports (`uncertain: true` with a reason).

## 7. Review page (parent PIN)

- List of source folders → files → staged items with counts: pending, approved, rejected, warnings.
- Item view side by side: the page render(s) on the left; the extracted record on the right as a readable preview plus an editable JSON box.
- Actions: **Approve**, **Reject**, **Save edit**, **Approve all without warnings in this file**.
- Approve writes the item to `questions` (upsert by ID, bump the version) or to `revision_cards`; the staged item records who approved and when.

## 8. Cost and limits

- Log tokens per page; show per-file totals after a run. Expect roughly 1,500–2,500 input tokens per page image plus output (an estimate to confirm on the pilot).
- Process pages sequentially or with small concurrency; retry once on API errors, then mark the page `failed` and continue.

## 9. Pilot acceptance

Run on: `science/sec1/mc-lss-assessment` (`qns_c01.pdf` + `answers.pdf`), `science/sec1/papers/2024_jwss_eoy.pdf`, `history/sec1/wisemann-comp-guide/rev_u01.pdf`, `geography/sec1/wisemann-comp-guide/rev_u01.pdf`. James reviews every item of the first file and spot-checks the rest. Target: at least 90% of items need no edit; no silent losses (every question number and table row accounted for as an item or a warning).
