# Student Interface Spec (v1, 18 Oct 2026)

*The student-facing app for go-live in January: a tutor-style session that runs itself, a clearer question screen, help on questions, an optional topic-limited discussion, and a Genshin-flavoured theme. For Claude Code. Builds on the existing Practise and Revise code; replaces their entry screens, not their engines.*

## 1. Devices and layout

- **Primary: the desktop PC with the 42" 4K screen**, browser full screen (see §10 for kiosk mode). Design for a wide screen at normal viewing distance: large type (body ≥ 20 px at 100% zoom, scales with the browser), generous spacing, a centred working area no wider than about 1600 px so the eyes don't travel across the whole screen.
- **Secondary: iPad in landscape.** The same layout at a smaller scale; touch targets ≥ 44 px; works with the on-screen keyboard and dictation.
- One responsive layout, no separate mobile design. No visible clock anywhere (existing decision); time on task is still logged.

## 2. Home screen ("Today")

One screen, no menus to explore. It shows:
1. A greeting and her **Adventurer Rank** progress bar (§8).
2. **One big button: "Start today's session"**, with the plan underneath in plain words, e.g. *"30 minutes: warm-up with 6 Science cards → Separation techniques: 4 questions → quick topic check."*
3. Two small secondary choices: **"Choose my own"** (the current topic picker, tidied: subject → topic list with status chips) and **"Year-end review"** (generated recall questions across taught topics, weakest first).
4. Today's **Daily Commissions** (§8) as three small ticks.

## 3. The session ("conveyor belt")

**Length:** she picks 30 or 60 minutes on the Start screen (parent can set the default per weekday). Sessions longer than 60 minutes are run as repeated blocks with a break screen between them ("Great work. Take 10 minutes: stretch, water. Continue when ready").

**The plan is built by code, in this order, until the time budget is used** (each step has a time estimate from her logged history, defaulting to 1.5 min per card and marks × 1.2 min per question):

| Step | What | Source |
|---|---|---|
| 1. Warm-up | Due Revise cards (box system), up to 8 | `card_progress` |
| 2. Focus topic | One topic: the parent's "focus this week" if set; else the taught topic with the lowest first-attempt score in the last 14 days; else the most recently taught topic | `topic_status`, attempts |
| 3. Learn | 2–4 revision cards from the focus topic she hasn't mastered (read → hide → recall) | revision cards |
| 4. Practise | Real (ingested) questions from the focus topic, mixing 1-mark and multi-mark, weakest command words first | questions, `source ≠ generated` |
| 5. Topic check | 3–5 generated recall questions on the focus topic | questions, `source = generated` |
| 6. Wrap-up | Summary: what went well, one thing to work on, Star Shards earned, what tomorrow will cover | — |

- **Subject rotation:** parent sets subjects per weekday (e.g. Mon Science, Tue Geography …); default rotates Science → Geography → History with Science twice as often.
- She can **skip a step** ("Not now") or **swap the focus topic** (picks from the 3 next-best suggestions). Skips are logged.
- **Progress is shown as steps, not time**: a slim bar "Step 3 of 6".
- If she leaves mid-session, the home screen offers "Resume session" the same day.

## 4. Question screen (Practise and topic check)

Two columns on wide screens, stacked on narrow ones:

```
┌──────────────────────────────┬──────────────────────────────┐
│ Question 2 of 4 · Science    │  Your answer                  │
│ Separation techniques · [4]  │  ┌──────────────────────────┐ │
│ Command word chip: EXPLAIN   │  │                          │ │
│ "Explain …"                  │  │                          │ │
│ Context / sources (scroll)   │  └──────────────────────────┘ │
│ [Figure / page ▸ expand]     │  [Help me]  [Submit]          │
└──────────────────────────────┴──────────────────────────────┘
```

- **Left:** question first (largest text), marks and command word chip, then context and sources, then the figure or page image as a thumbnail that expands to full screen. The page image is never the first thing she sees.
- **Right:** answer box sized to the marks (about 3 lines per mark), with a gentle note under it from the command-word table ("Explain [4]: about 2 linked points, each with a reason").

## 5. Feedback (so nothing is missed)

After Submit, the right column becomes feedback, shown **in steps she clicks through**, not one long block:

1. **Score card:** score, confidence, and **one headline: "The one thing to fix"** (the most valuable missed point or technique issue, chosen by code from the marking JSON: a missed key term > a missed point > a technique note).
2. **Your answer, marked:** her text with earning phrases highlighted green and the mark point each earned; filler greyed.
3. **What was missing:** missed points and key terms.
4. **Full-marks answer.**
5. **Rewrite** (when marks were lost, as now; skippable).

Each step has a **"Got it →"** button; the next step appears below the previous ones (they stay visible). The final step ends with "Next question". Keyboard: Enter = Got it.

## 6. "Help me" on questions

A **Help me** button on every question, before she submits. Three tiers, one at a time:

| Tier | Gives | Built from |
|---|---|---|
| 1. Explain the question | The question in simpler words, what the command word asks for, what the marks mean (how many points or how much explanation), and which part of the topic it is about. Gives no part of the answer; never sees the mark scheme | `LIGHT_MODEL`, from what she can already see; cached per question |
| 2. Nudge | Which idea or area to think about, without the answer ("Think about what happens to particle movement when …") | `LIGHT_MODEL`, from the mark points; cached per question |
| 3. Show me the card | Opens the related revision card (same topic, best text match) in a side panel | Code |

- Any help used marks the attempt **"with help"** (score stands, shown to parent).
- Tier 2 is skipped for MCQs and when the parent turns it off (tier 1 then goes straight to the card). Code throws away a reply that states a mark point or uses a key term the question doesn't.
- After submitting, **Help me** becomes **"Explain this"**: the existing follow-up call (why a point was or wasn't awarded).
- Parent setting: Help me on/off; tier 2 on/off.

## 7. "Ask about this" (limited discussion)

An optional chat panel on the question and Revise screens. **Off by default; parent switches it on** in settings.

- **Scope:** the current question or card, its topic's approved revision cards and, after submitting, her answer and the marking. The system prompt (cached) tells the model to answer only about that topic, at Sec 1–2 level, in short replies (≤ 80 words), to guide rather than give answers before she has submitted, and to decline anything else with one friendly line ("Let's stick to separation techniques. Ask me about this question!").
- **Code guards** (not just the prompt): max 6 messages from her per question or card; max 40 per day (parent setting); each message ≤ 300 characters; the conversation is cleared when she moves on; nothing outside the scope payload is sent to the model.
- `LIGHT_MODEL`. Every conversation is saved and **visible to the parent** (dashboard later; for now a simple list page behind the PIN).
- Cost: about 1–2 US cents per conversation; the daily cap bounds it.

## 8. Theme and gamification (Genshin-flavoured, original art)

**Visual theme:** a fantasy-adventure look in the spirit of Genshin: parchment-and-gold panels, soft gradients, starry accents, one **element colour per subject** (Science: electric violet, Geography: earthy amber, History: wind teal). Fonts: a serif display font for headings and a clean sans for body text (both from Google Fonts or self-hosted).

**Copyright rule (repo stays clean):** no Genshin characters, logos, artwork, music or official names in the code or repo. Everything shipped is original. For private home use, the parent can **upload their own images** (e.g. a favourite-character picture as her companion avatar) through settings; uploads live in the data directory, never in git.

**Mechanics (no gambling-style mechanics: no random "wishes" or loot boxes):**
- **Adventurer Rank:** XP from effort, not just marks: finishing a session step, a rewrite done, a card moved up a box, a topic check passed. Rank-ups unlock cosmetic rewards (panel frames, title badges, background scenes).
- **Star Shards:** earned per session; spent on cosmetic unlocks in a small **Reward Shop** the parent stocks (cosmetics, plus optional real-world rewards the parent defines, e.g. "30 min game time", approved by the parent when redeemed).
- **Daily Commissions:** 3 small daily goals (e.g. "Finish today's session", "Do one rewrite", "Pass a topic check"); all 3 = bonus Shards.
- **Region map of mastery:** each subject is a "region"; each topic is a waypoint that lights up as its topic status rises (not started → learning → secure). Shown on the home screen as a small map and full screen on tap.
- **Companion:** a small guide character (original placeholder art, or the parent's uploaded image) who delivers the "one thing to fix" and session summaries in short friendly lines.

**K-pop (supporting, optional):** a parent toggle for "stage" celebration screens on rank-ups (lights and confetti, no lyrics, no artist images unless the parent uploads them).

## 9. Parent settings added (behind the PIN)

Default session length per weekday; subjects per weekday; "focus topic this week" per subject; Help me on/off and tier 2 on/off; Ask about this on/off and daily cap; Reward Shop items; uploaded images (companion, backgrounds); K-pop celebrations on/off.

## 10. Distractions (setup advice, not code)

- **PC:** run the browser in kiosk mode for her sessions (e.g. Chrome `--kiosk http://<server>:3002`), in a separate OS user account with only that shortcut. Parent exits with the OS key combination.
- **iPad:** iOS Guided Access can lock the iPad to one app during a session (if the school's device management allows it).

## 11. Data

- `sessions`: id, started_at, ended_at, length_planned, plan JSON, steps completed/skipped, focus topic.
- `hint_uses`: attempt or card, tier, created_at.
- `discussions`: id, scope (question/card id), messages JSON, tokens, created_at.
- `rewards`: xp, rank, shards balance, unlocks, commissions per day, redemptions (parent-approved).
- Time per step logged; still no visible clock.

## 12. Out of scope (v1)

Parent dashboard views (separate spec, uses these tables); speaking practice; timed exam mode; multiplayer or sharing; any visible countdown.

## 13. Tests (no API calls)

Plan builder (time budget, step order, focus-topic choice, subject rotation, skips, resume); feedback "one thing to fix" selection; help tiers and "with help" flag; discussion guards (message caps, length, scope payload contents, toggle off hides the panel); XP and Shards rules, commissions, rank-ups; break screen after 60 minutes; layout renders at 1280×800 and 3840×2160 (template smoke tests).

## 14. Build order (separate Claude Code tasks)

1. Session planner + home screen + session flow (§2–3).
2. Question screen layout + stepped feedback (§4–5).
3. Help me (§6).
4. Ask about this (§7).
5. Theme + gamification (§8) + parent settings (§9).
