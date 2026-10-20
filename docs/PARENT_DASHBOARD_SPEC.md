# Parent Dashboard Spec (v1, 20 Oct 2026)

*One PIN-protected page that answers, in a few minutes on a Sunday evening: is it working, where is she weak, how is her exam technique, is she using help sensibly, and what is it costing? For Claude Code. Read-only over existing tables, plus one-click actions that reuse existing settings.*

## 1. Principles

- **Built from data already logged** (attempts, marking JSON, recall attempts, sessions, hint uses, discussions, rewards ledger, API token logs). No new model calls.
- **First attempts are what count** for every score (rewrites shown separately).
- **Period filter** at the top: 7 days (default), 30 days, 90 days, all. Every section respects it.
- **Plain numbers and small charts.** Charts are server-rendered inline SVG (no chart library, no CDN). Every chart also has its numbers as text.
- **Weakest things are flagged**, so you can act without reading everything.
- Times shown in Asia/Singapore.

## 2. Sections (top to bottom)

### 2.1 This period at a glance
Tiles: sessions completed (and started), total time on task, questions answered (first attempts), **average first-attempt score %** (with the change against the previous period), cards recalled, topic checks passed, days active out of the period.

### 2.2 Score trend
Weekly average first-attempt score % per subject (three lines), with a **75% target line** and a **45% starting line** for reference. Below: the same per marking mode (points vs LORMS) for History and Geography.

### 2.3 Progress by topic
Table per subject: topic, map state (not started / learning / secure), first attempts (n), average first-attempt %, trend (▲▼● vs previous period), recall rate on cards, cards due now, last activity.
- **Flag** (highlight) the 3 weakest taught topics per subject: lowest average with at least 3 attempts, or no activity in 14 days.
- Each row has a **"Make this week's focus"** button (sets the existing focus-topic setting for 7 days).

### 2.4 Exam technique
The core of the 45%→75% goal, from the marking JSON:
- **Command words:** average % by command word (state, describe, explain, compare …), weakest first.
- **Missed key terms:** the 10 most often missed, with the topic.
- **Reversed statements** (wrong way round): count and the 5 most recent examples (question + her phrase).
- **Waffle:** share of answers with filler quotes; average filler words per answer.
- **Length vs marks:** for multi-mark questions, answers that were too short (fewer points than marks) or far too long.
- **Rewrites:** how often offered, done vs skipped, and the average gain in marks from a rewrite.
- **LORMS:** distribution of levels reached; the most common "next level needs" from the teaching explanations.

### 2.5 Help and discussion
- Help me usage by tier (explain the question / nudge / show card) per subject, and the **share of attempts "with help"** with their average score vs without help.
- Ask about this: number of conversations and messages, top topics, with a link to the existing discussions page.
- **"Looks wrong" reports** awaiting your review (count, link).

### 2.6 Habits and timing
- Sessions by day and time of day (a small heatmap: weekday × hour).
- Steps completed vs skipped per session type; most-skipped step.
- Average time per mark by subject (parent view only; she never sees a clock).

### 2.7 Revise
Cards seen, known (box ≥ 3), due now, recall rate by method (typed / paper / parent quiz), quick-fire accuracy.

### 2.8 Rewards
Current rank, Shards balance and held, pending real-reward requests (link to approve), commissions completed this period.

### 2.9 Cost
- API cost this month in US$ and SG$ (fixed conversion rate in config), split by feature: marking, teaching explanations, hints, Ask about this, Help me remember, MCQ explanations, ingestion, generation.
- **Projected month-end** at the current daily rate, against the budget: **SG$100 total including the subscription** (subscription amount in config). Colour turns amber at 80% and red at 100%.

## 3. Data notes

- Use existing tables; add only views or helper queries. If any figure needs a column that doesn't exist (e.g. feature tag on API cost logs), add it with a migration and backfill what can be inferred; label it in the summary.
- Queries must stay fast with a year of data: add indexes where needed.

## 4. Layout

- Desktop first (the 42" screen), readable on the iPad. Same theme stylesheet, plainer panels.
- A sticky header with the period filter and section links.
- Each section collapsible; collapsed state remembered in the browser session only.

## 5. Out of scope (v1)

Emailed or pushed weekly reports; exporting data; comparing against other students; predictions of exam grades.

## 6. Tests (no API calls)

Fixture database with made-up attempts, cards, sessions, hints, discussions, rewards and cost logs. Check: period filter boundaries in Asia/Singapore time; first-attempt-only scoring; weakest-topic flags; command-word and key-term aggregation from marking JSON; with-help vs without-help split; cost split, projection and budget colour thresholds; "Make this week's focus" sets the setting; page needs the PIN; SVG charts render with their text numbers.
