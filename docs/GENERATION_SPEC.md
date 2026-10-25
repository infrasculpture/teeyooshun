# Question Generation Spec (v1, 15 Oct 2026)

*Text-only practice questions generated from approved revision cards, to fill the gaps ingestion leaves. For Claude Code. Examples here are made up.*

## 1. Purpose and scope

Test knowledge and answer-writing on every taught topic, at the right length for the command word and marks. Questions are built **only from her own approved revision cards**, so the content and the marking match what she revises.

| In v1 | Not in v1 |
|---|---|
| Science and Geography: 1-mark recall, 2–4 mark describe/explain/compare (points mode) | Picture, map, graph or diagram questions |
| History: 1–3 mark knowledge questions (points mode) | History source-based questions (no invented sources, ever) |
| "Using an example / with reference to a named place" questions where the card has examples | History structured LORMS questions (v1.1, with level templates shared with the deferred History-book work) |
| | MCQ generation |

## 2. Inputs

- **Card:** approved `revision_cards` row: prompt, command word, answer points and sub-points, key terms, examples, notes, topic, subject, level. Image cards and cards with fewer than 1 usable point are skipped.
- **Style examples:** up to 5 approved real questions (ingested from books or papers) from the same subject, preferring the same topic, with their marks and mark points. They set phrasing, command words and mark sizes. Chosen by code; cached in the prompt per topic.
- **Command-word glossary:** from the syllabus (state, name, define, describe, explain, compare, suggest …), with the expected answer length per mark.

## 3. What to generate per card

Code decides the mix from the card's size; the model writes the questions.

| Card has | Generate |
|---|---|
| 1 point (a definition or fact) | 1 question, 1 mark (define / state / name) |
| 2–3 points | 1 recall question (1 mark) + 1 question worth 2–3 marks |
| 4+ points | 1 recall question (1 mark) + 1 question worth 3–4 marks using "any N" (`choose`) when the card has more points than marks |
| examples block | optionally 1 "using an example" question (2–3 marks) |

Cap: 3 questions per card. A grouped card counts each group heading as a point.

## 4. Rules for the model (system prompt, cached)

1. Use only facts on the card. No outside facts, dates, names or numbers.
2. Every mark point must be one of the card's points (or sub-point), worded so the key terms are kept exactly. Mark point `key_terms` come from the card's key terms.
3. The question must be answerable from the card alone, without seeing the card's wording in the question (don't give the answer away).
4. Marks follow the house style: about one mark per distinct point; the command word must suit the marks (no "explain" for 1 mark, no "state" for 4).
5. Phrase like the style examples: plain Singapore-school English, the `[n]` marks convention, no trick questions.
6. Also return a `full_marks_answer`: the shortest answer that earns full marks.
7. Return strict JSON only (small schema, one call per card; see §6).

## 5. Checks in code (before staging)

- Each mark point overlaps a card point (text similarity above a threshold); otherwise drop that point and, if marks now exceed points with no `choose`, drop the question with a warning.
- Marks ≤ number of mark points, or `choose` set.
- Command word is in the glossary and suits the marks (table in code).
- Not a near-duplicate of an existing question (approved or pending) in the same topic: simple text similarity; flag `possible_duplicate`.
- Question text does not contain the answer verbatim (overlap check against mark points); flag `gives_answer_away`.
- The question text and context (as the model wrote them) contain at least one key term or named entity from the card (key terms, capitalised names, what a table compares, content words of the section heading and prompt); otherwise flag `lost_context` (a warning, so it is excluded from "approve all without warnings").
- The context starts with the card's section heading, or the topic title if the heading is empty.

## 6. Output and staging

- Same question record as the Marking spec §2: `marking_mode: points`, `mark_points`, `choose`, `marks`, `command_word`, `topic`, `context` (empty or a short scene-setting line), plus:
  - `source: generated`, `origin_card_id`, `origin_card_version`.
- ID: `gen-<card id>-q<n>`; stable for a card version (re-running doesn't duplicate).
- Staged in `staged_items` as `pending`, like ingestion. Nothing reaches her until approved.
- Keep the structured-output schema small (only the fields above), to stay well under the API's grammar size limit.

## 7. Command

```
docker compose run --rm app python -m app.generate <subject> [--topic <topic id>] [--level sec1] [--limit N] [--dry-run]
```
- Processes approved cards with no generated questions for their current version.
- `--dry-run`: lists cards and the planned question mix, with a cost estimate; no API calls.
- `--regenerate`: instead of new cards, redoes the selected cards that have **pending** generated questions: those are superseded and new ones generated with the current prompt. Approved and rejected questions are never touched (their slots are not asked for again). With `--dry-run` it reports how many pending questions would be replaced and the cost estimate.
- Model: `MARKING_MODEL`. Logs tokens and cost per card and in total.

## 8. Review page

- A "Generated" filter. Each item shows the question, marks, mark points and full-marks answer, **with the origin card beside it**.
- Actions: Approve, Reject, Save edit, **Regenerate** (new call for that card), **Approve all without warnings for this topic**.

## 9. Confidence and use in Practise

- Base confidence for `generated` is Low (Marking spec §5). **Setting `generated_approved_confidence`** (default `medium`): once the parent has approved a generated question, it is treated as Medium. *(Open decision for James; default as recommended.)*
- Practise draws from all approved questions in taught topics; generated questions are labelled "Practice question" in the parent view only (she sees no difference).
- **Card changes:** if a card gets a new version, its generated questions are flagged `stale` on the review page (still usable until the parent regenerates or rejects them).

## 10. Cost

About 1–2k input tokens (mostly cached) and 300–600 output tokens per card on the marking model: roughly US$0.005–0.01 per card, so about US$5–10 for all Sec 1 cards. Marking generated questions costs the same as any other question.

## 11. Pilot and acceptance

Pilot: one Geography unit (about 40–50 cards). James reviews every question.
- At least 80% approved without edits.
- No question uses a fact not on its card.
- Mark sizes and command words look like the style examples.
- Then: 10–15 generated questions answered with made-up answers through the normal marker; marks look right (a mini calibration).

## 12. Tests (no API calls)

Mix rules by card size; mark-point overlap check drops unknown points; marks vs points vs `choose`; command word vs marks table; duplicate and gives-away flags; stable IDs per card version; stale flag on a new card version; dry-run makes no calls; image cards skipped.
