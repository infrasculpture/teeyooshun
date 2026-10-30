# Ingestion Stage C Spec (v1, 7 Oct 2026)

*Notes and slide handouts → revision cards. Extends `docs/INGESTION_SPEC.md` (Stages A and B). For Claude Code. Real files stay in the server data directory; examples here are made up.*

## 1. Scope

| Material type | Typical source | Becomes |
|---|---|---|
| `school_notes` | School workbooks (A4 pages: explanations, tables, diagrams, activities) and slide handouts | Revision cards + reference passages |
| `teaching_text` | Prose-style books (e.g. a Sec 2 Geography book with no revision tables), textbooks later | Same as `school_notes` |

Out of scope: questions found in notes (quick checks, exercises, activities) – they are **not** staged in v1; handwritten content of any kind; worksheets with answer keys (still the Stage A `questions` path).

## 2. Ground rules

1. **Printed content only.** All handwriting is ignored: fill-ins, margin notes, ticks, answers, names. The extraction prompt says so explicitly; nothing handwritten appears in any card, reference passage or warning text.
2. **Never extract names.** Names, class and index numbers are ignored wherever they appear (not only on cover pages). Code additionally drops any extracted line matching `name|class|index no` patterns.
3. **Cards are derived, not copied.** Unlike revision tables, Claude writes the prompt from the printed content. Each card is marked `derived: true` and must use the printed wording for its points and key terms; no facts that are not on the page.
4. **Incomplete printed statements are skipped.** A sentence with a blank to fill in is not used for a card point. The page gets the warning `has_blanks` so the parent knows some content may be missing.

## 3. Page and slide handling

**Page classes** (extends Stage A): `cover`, `contents`, `learning_outcomes`, `glossary`, `content`, `activity`, `questions`, `image_only`, `blank`, `other`.
- `cover`, `contents`, `blank`, `other`: skipped (renders of covers deleted, as now).
- `learning_outcomes`: printed outcomes stored in `topic_outcomes` (topic, text, source_ref). No cards. Used later by the dashboard and coverage checks.
- `glossary`: one **Define** card per term, answer = the printed description verbatim (format `bullets`, one point), key term = the term.
- `content`: cards (see §4) + reference passages.
- `activity`, `questions`: no cards, no questions staged; logged as skipped with the page number.
- `image_only`: one image card if the image carries teachable content (labelled diagram, apparatus, table-as-image); otherwise skipped with a note.

Ignored on any page: QR codes, URLs, "read textbook pages …" pointers, decorative images.

**Slide handouts** (`layout: slides_4up`):
- Code crops each page into four slide images **before** any API call (free). Crop by quarters with a small inset; if a quarter is blank, mark it and skip.
- Order comes from `slide_order` per file: `down_then_across` (TL, BL, TR, BR) or `across_then_down` (TL, TR, BL, BR). No default guessing beyond the source.yaml default.
- Each slide image is extracted on its own at full resolution. Slides are numbered `s01, s02 …` in reading order.
- **Divider slides** (a section title with three or fewer bullets of sub-headings, e.g. "Element of weather: Temperature") set the `section` for following slides and produce no card themselves.
- Cards may draw on **up to three consecutive slides** in the same section when one idea spans them (the stitch step joins them; same continuation rule as Stage B).
- The review page shows the four crops of each page in the order used, so a wrong `slide_order` is obvious at a glance; changing it in source.yaml and re-running regenerates that file.

## 4. Cards from content

- Same card shape as Stage B (`docs/card_format_example.json`), plus: `derived: true`, `origin: school_notes | teaching_text`, `source_ref` with page (and slide) number.
- What makes a card: a definition; a printed table (format `table`, rows as points – e.g. "differences between weather and climate", "solution vs suspension"); a process or method (`list`); a set of properties, causes or effects (`bullets`/`grouped`); a skills table such as "how to describe a climograph" (`table`, command word `describe`); formulae (`bullets`, each formula one point; command word `state`).
- `command_word`: chosen from the syllabus glossary to fit what an exam would ask (define / state / describe / explain / compare …).
- Long answers split as in Stage B (> about 8 points).
- **Likely-duplicate warning:** when an approved card in the same topic has a prompt or point set that closely overlaps (simple text similarity in code, no API), stage with `possible_duplicate: <card id>`; review shows both side by side.

## 5. Reference passages

From every `content` page or slide, also store the printed text as plain passages in `reference_passages` (id, subject, level, topic, source_ref, text, page render ref). Same API call, no extra cost. Not shown to her yet; used later by "Help me remember" and the hint button for grounding ("your notes say …"). Handwriting rules apply.

## 6. `source.yaml` additions

```yaml
title: Sec 1 Geography school notes
publisher: School
subject: geography
level: sec1
files:
  "notes_*": school_notes
key_reliability: school
file_options:                         # optional, per file (exact name or glob)
  "notes_weather-climate.pdf":
    layout: slides_4up                # or a4 (default)
    slide_order: down_then_across     # or across_then_down; required with slides_4up
    topic: geo-xx-weather-climate     # optional; otherwise the model assigns a topic per card
```

- Files may be named by topic instead of number: `notes_<topic-word>.pdf` (e.g. `notes_weather-climate.pdf`). Card IDs use the file stem as now.
- No `units` needed: if neither `units` nor `file_options.topic` covers a file, the model picks the topic per card from the subject/level topic list (as for exam papers). Low-certainty choices get `topic_uncertain`.
- Validation: `slides_4up` without `slide_order` is an error for that file.

## 7. Warnings (review page)

`has_blanks`, `possible_duplicate`, `topic_uncertain` (derived cards carry a `derived` flag, shown as a "derived" badge on the review pages, but no warning, so approve-all-clean can approve them), plus existing Stage B warnings. Skipped pages listed per file with their class.

## 8. Cost

Roughly the Stage B rate per page or slide (an estimate: about US$1 for a 30-page workbook, under US$1 for a 7-page 4-up handout). Logged per file as now.

## 9. Pilot acceptance

Run on one Science workbook chapter (A4) and one Geography slide handout.
- No handwriting and no names in any staged output.
- Every printed glossary term has a Define card.
- Every printed table on a content page/slide is covered by a card or a warning.
- Slide order on the review page matches the deck.
- At least 80% of derived cards need no edit (lower than Stage B's 90% because prompts are composed).

## 10. Tests (no API calls)

4-up cropping and both slide orders; blank-quarter skipping; page-class routing (activity/questions produce nothing; glossary produces one card per term from a mocked response); name-line filter; duplicate detection; `file_options` resolution (exact name beats glob); `slides_4up` without `slide_order` fails validation; reference passages stored alongside cards.
