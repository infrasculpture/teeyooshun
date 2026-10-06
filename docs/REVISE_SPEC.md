# Revise Mode Spec (v1)

*Task 5. The card-based read → hide → recall → check workflow, alongside Practise mode. For Claude Code. Cards come from `revision_cards` (approved only). Examples here are made up.*

## 1. Purpose
Help her learn small chunks of content and reproduce them in her own words with the right key terms, at the right length for the command word. Missed cards return sooner; known cards return later.

## 2. Session flow
1. **Start:** she picks subject → topic(s) (only taught topics are offered, "not taught" excluded, as in Practise). The app builds a set of N cards (setting, default 4): cards due for review first, then new cards in book order.
2. **Study:** each card shows its prompt, command word chip, answer (rendered by format, §4), key terms in bold, examples and notes, and a page-image button. Optional **underline step** (setting): she taps the words she thinks matter; code compares with `key_terms` and shows hits and misses. No API call.
3. **Recall:** answers hidden; prompt only. She chooses how to answer (default from settings):
   - **Type** (or dictate): submit text.
   - **Paper:** writes on paper, taps "Check".
4. **Check:**
   - Typed → marked against the card's points with `LIGHT_MODEL`, reusing the points-mode engine (each top-level point or group heading = one mark point; key terms required as in the marking spec; verbatim-quote rule applies). Shows points covered and missed, key terms missed, a one-line command-word note ("Define: one precise sentence is enough").
   - Paper → answer reappears with a tick box per point; she ticks what she wrote. No API call.
   - Image cards (`recall: self_tick_only`) always use self-tick.
5. **Result:** card moves box (§5). After the set: summary (cards known / to revisit) and "Another set" or "Done".

## 3. Parent quiz view
- Parent-only page (PIN). Pick subject/topic or "her due cards".
- Shows one card at a time: prompt in large text, answer points underneath, each with a tick box; "Done" moves on.
- Saved as a recall attempt with `method: parent_quiz`; moves boxes like any attempt.

## 4. Rendering by format
| Format | Study view | Check unit (one "point") |
|---|---|---|
| bullets | bullet list, sub-points indented | each top-level point |
| list | numbered list | each item |
| grouped | heading + points under it | each group heading (details are stretch) |
| table | table | each row |
| timeline | date → event list | each event |
| image | image (page region) + alt text | whole card (self-tick only) |

Split cards show "part k of n" and link to their siblings.

## 5. Spacing (box system)
- Five boxes. New cards enter box 1. All points covered (or ≥ 80% for multi-point cards) → up one box; otherwise back to box 1.
- Review intervals by box (setting, defaults): 1 day, 2 days, 4 days, 8 days, 16 days. A card is "due" when its interval has passed since the last attempt.
- **Exam mode** (setting/toggle): ignores intervals and draws from all taught cards in the chosen topics, weakest first.

## 6. Settings page (parent, no code changes needed)
Cards per set; default recall method (type / paper); underline step on/off; box intervals; ≥% threshold for "known"; exam mode on/off. Stored in the database with defaults.

## 7. Data
- `recall_attempts`: card_id, card_version, method (`typed` / `paper` / `parent_quiz`), points_total, points_covered, covered point ids, key_terms_missed, typed_text (if any), seconds_taken, box_before, box_after, created_at.
- `card_progress`: card_id, box, last_attempt_at, due_at.
- Typed checks log tokens like other API calls.

## 8. Dashboard hooks (for the later dashboard task)
Per topic: cards seen / known / due; recall rate by method; command words most often missed (from the command-word notes); trend over time. Store enough now; build the views later.

## 9. Out of scope for this task
Context-and-hint button with teaching-text references (later; §11 is the card-only first step); timed paper recall; theme and badges; speaking practice.

## 10. Tests
Box movement and due dates; set building (due first, then new, taught topics only); self-tick scoring; typed check uses the points engine; parent quiz saves attempts; settings change behaviour without code. Made-up cards only; no API calls in tests.

## 11. "Help me remember" (added 6 Oct 2026, Task 5b)

**Purpose:** before recall, show her why a card matters, where the marks are, the usual slip and one way to remember it.

**UI:** a "Help me remember" button in the study view (and in exam mode). It opens a panel with four short labelled parts:
1. **Why this matters**: 1–2 sentences of plain context.
2. **The marks are in**: which points and key terms an examiner looks for, and what would be padding.
3. **Common slip**: a likely mistake (reversal, vague word instead of the key term, too short or too long for the command word).
4. **Memory hook**: one technique (acronym, chunking, a story, a cause → effect chain, a link to the command word). Leave it out for one-point cards.

**Generation:**
- One `LIGHT_MODEL` call, strict JSON `{why, marks_in, common_slip, hook}` (`hook` may be empty).
- Input: only the card (prompt, command word, answer points, key terms, examples, notes), topic name, subject and level. The system prompt (cached) tells the model to add no facts beyond the card, use the card's key terms exactly, write for a 13-year-old, and keep each part to 2 sentences or fewer.
- **Cached per (card_id, card_version)** in a `card_help` table (`card_id`, `card_version`, JSON, model, tokens, created_at). First open generates it; later opens are free. A new card version generates fresh help.
- On API failure: show "Couldn't load help, try again"; the session carries on.

**Follow-up box (optional, under the panel):** "I still don't get…". A short `LIGHT_MODEL` reply with the card and the cached help as context. Same no-new-facts rule. Not cached. At most 3 per card per set; on the standalone card page (no set), at most 3 per card per day (rolling 24 hours).

**Pre-generate and review:** a command `python -m app.revise_help <topic or source folder>` generates help for every approved card that lacks it. The parent review page lists help per card with **Edit** and **Regenerate** buttons. Edited help is kept, marked `edited_by_parent`, and not overwritten until the card version changes.

**Looks wrong:** the panel has the existing "looks wrong" button; flags go to the parent review list with the card and help text.

**Data and logging:**
- `help_views`: card_id, card_version, set_id (empty when opened from the standalone card page), opened_at, followups_used.
- A recall attempt gets `with_help: true` (new column on `recall_attempts`) if a `help_views` row exists for the same card (any version) opened in the 24 hours before the attempt, wherever it was opened (in a set or on the standalone page). Box movement is unchanged; the dashboard can separate the two later.
- All calls log tokens like other API calls.

**Tests (no API calls):** cache hit returns stored help without calling the model (mock); new card version triggers regeneration; `with_help` set correctly (help opened within 24 hours, in a set or standalone, any card version; not set when opened earlier or for another card); follow-up limit enforced (3 per card per set; 3 per card per day on the standalone page); parent edit survives regenerate-all; one-point card accepted with empty hook; API failure path renders the retry message.
