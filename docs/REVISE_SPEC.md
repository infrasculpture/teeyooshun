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
Context-and-hint button (built later with teaching-text references); timed paper recall; theme and badges; speaking practice.

## 10. Tests
Box movement and due dates; set building (due first, then new, taught topics only); self-tick scoring; typed check uses the points engine; parent quiz saves attempts; settings change behaviour without code. Made-up cards only; no API calls in tests.

## 11. Decisions made when building (Task 5)
- **Taught topics.** A parent sets each topic to taught / not taught / not set on the settings page (`topic_status` table). "Not taught" hides the topic in **both** Practise and Revise; "not set" topics are offered as normal. Her own per-session reporting of what was taught comes later and will write to the same table; the settings page stays as the parent override.
- **When she recalls** is a setting, `recall_timing`: `after_set` (default: study the whole set, then recall each card) or `after_each` (study a card, recall it, then the next). The value is stored on the set when it starts, so changing it mid-set doesn't change that set.
- **Sets** are stored in `revise_sets` (kind `student` or `parent_quiz`, subject, topics, card ids, exam mode, recall timing). A card can be answered once per set. A student cannot open a parent's quiz set and the quiz pages cannot open a student set.
- **Command-word notes** are fixed text chosen by code, with no API call, and shown only when her typed answer gives a reason to: *define* over two sentences; *state / name / identify / give / list* over about 15 words per point; *explain* with no reason word ("because", "so", "therefore" and similar); *describe* under about 4 words per point; *compare* with no comparing word ("whereas", "both", "than" and similar); *outline* with more than two sentences beyond the number of points. The note shown is stored on the attempt (`command_note`) for the dashboard.
- **Grouped cards.** A group is covered if she gives the heading idea or any one point under it. Typed checks pass the points under the heading to the marker as alternatives.
- **Table cards.** Key terms are stored for the whole table; a row's key terms are the ones that appear in that row.
- **Known** on the summary means the card moved up a box (anything but back to box 1). A single-point card needs its point; a multi-point card needs the threshold percentage.
- **Exam mode** attempts move boxes and due dates like any other attempt. "Weakest first" is: never seen, then lowest box, then lowest average recall rate, then longest since last seen. It can be ticked on the start page or made the default in settings.
- **Book order** for new cards is file name, then file page, then position on the page (`revision_cards.sort_key`, set on approval; cards approved before Task 5 get theirs from their staged item).
- **Underline step.** A key term of several words counts as spotted if she taps any of its words. Nothing is stored or scored.
- **A failed typed check** saves nothing; she can try again or tick it herself.
- Extra columns on `recall_attempts` beyond §7: `set_id`, `command_word`, `command_note`, `result_json` (the checked marking result) and `api_call_id`.
