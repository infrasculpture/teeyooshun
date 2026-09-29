# Project Brief: Teeyooshun (SG MoE SEC Tuition Support)

*Living document. Last updated: 29 Sep 2026 (second revision). App name: **Teeyooshun** (also the GitHub repo name). Sections 1–7 are James's stated requirements and constraints. Section 8 is Claude's recommendations (unconfirmed unless listed in the decisions log). Section 9 is open questions and outstanding work. Section 10 is the decisions log.*

## 1. Purpose and success measure

Build a home-hosted app that gives a Singapore Secondary G3 (Express, SEC pathway, not IP) student tuition-style practice, replacing or reducing paid tuition. Science first; Geography and History later.

**Core objective:** help her write multi-mark answers that hit every mark, with no waffle and no four-word answers.

**Success measure:** raise her assessment scores from about 45% to about 75% on each assessment. The target is not 90%+. Marking does not need to be perfect: James is loose about what counts as a mark and relies on his own judgement and the answer key, so any improvement on that is good. "Don't let perfect be the enemy of the good."

## 2. The student and situation

- Currently in Secondary 1 (sitting Sec 1 exams now); starts Secondary 2 in January.
- Three or four assessment sets a year (Weighted Assessments plus finals; WA4 this year).
- Works slowly; needs coaxing to work faster while staying accurate.
- Retains information but has to re-learn a lot before exams because it isn't kept fresh through the year.
- Learns best from repeated questioning, proper explanations, and knowing where the marks come from.
- **Where marks are lost:** knows the topic well by year-end but loses marks to "silly mistakes": wrong terminology, diagrams or concepts the wrong way around. Sometimes misreads a 1-mark question and needs help relating it to what she knows. On 2–3 mark questions she writes a lot of filler without the terms the mark needs; sometimes writes only a few words where a full sentence is needed. Weak time management on papers (left about half of a History paper unanswered recently); tends to dip in the afternoon.
- Timed past-paper practice is done separately, so this app is for year-round learning and reinforcement.
- Usage: sessions of 30–60 minutes (she needs refocusing after about an hour); target 60–120 minutes per subject per week, more approaching exams; up to three hours per week overall. James can be on hand to refocus her.
- Device: iPad preferred, Windows laptop possible. Typed answers are fine. Text-only questions are fine for now; she can do assessment-book questions on paper and use the app for support.
- Engagement: responds well to streaks (long Duolingo streak) but won't use the app daily. Interests: Genshin Impact (Mitya and Gorou most important; also Vesna, Lohen, Chongyun, Paimon; any character or region fine) and K-pop (KickFlip favourite; Stray Kids, BTS, TWS, Enhypen; any 4th/5th-gen group including girl groups).

## 3. Why not tuition

Expensive; time and travel, plus a free parent for lifts; quality is hard for parents to assess. What tuition does well: keeps pace with school modules, revisits recent modules, and drills assessment-book questions with explanation.

## 4. Previous attempt (ChatGPT project, now binned)

Scanned assessment/study books plus past papers and answer keys were uploaded. ChatGPT asked questions worth 2+ marks, then marked each answer by showing: (1) the exact wording that earned marks; (2) wording that didn't and why; (3) what is needed for full marks and why; (4) the exact terms and concepts required; (5) answers to follow-up questions; (6) extra syllabus-aligned information on request. Its answers were very accurate. It failed because it couldn't follow the instructions reliably, and it couldn't read all pages of the image-only PDF scans. No examples were kept.

## 5. Requirements

**Student**
1. Ask about a concept.
2. Be taught a little about a topic or module content, MoE syllabus-aligned, on request.
3. Request a question (single or multiple mark), from a past paper or made up but syllabus-aligned (a mix is fine), with the marking described in section 4.
4. See a running score; questioning reacts to weak topics and suggests more questions there.
5. Pick only topics completed at school, plus preview upcoming ones. The school tells parents nothing, so she reports completed topics at the start of each session; a parent can override; any topic can be marked "not taught" (her school sometimes swaps in modules not in the assessment books). A "We weren't taught this" button on every question and topic lets her flag gaps as she meets them; flags appear on the dashboard so parents can see what the school has skipped or dropped.
6. **Cumulative levels (added 29 Sep):** exams in Sec 2, 3 and 4 build on earlier years, so the app must hold Sec 1 and Sec 2 material together, test across both, and accept Sec 3 and Sec 4 material in later years.

**Marking**
- Show a confidence level with each mark, so loose marking is visible.
- "This marking looks wrong" button.
- After a multi-mark answer loses marks, prompt a rewrite, with a skip button (James will coach her not to skip).
- Question-reading hints for questions she misreads.
- History is marked by LORMS (level of response), typically using PEEL; Sec 1 Geography has one LORMS question per paper (descriptive, levels driven by breadth, depth and specificity of examples); everything else is point-marked.

**Session**
- Short spaced review of older topics at the start of a session, with an option to skip (exam time).
- No visible pace clock or timer (could add anxiety). Pace appears in the stats only.

**Parent**
1. Proof that work was done (a dashboard is preferred to a password).
2. How much of each topic has been covered, and how much work done.
3. Success rate by topic, to see whether weakness is understanding or source knowledge.
4. Score trends by topic and module over time, using first-attempt marks (the likely-grade indicator); rewrite gains tracked separately.
5. Time-of-day view (does performance dip in the afternoon?).
6. Interactive assessment of performance and advice on further study from Claude (in-app or in Claude.ai, whichever works best).
7. A key of every badge and title and its unlock condition, so rewards can be arranged offline.
8. How often she skips rewrites (an engagement signal).
9. "We weren't taught this" flags, by topic.

**Dashboard:** behind a simple PIN, no full authentication, as long as she can't easily tamper with the data. She and James review stats together, so they needn't be secret, just out of the way. Two parents; James has most of the responsibility.

**Gamification:** themed on Genshin Impact and K-pop, slipped in here and there, never overdone. Names, song titles, places and lingo only; no lyrics, including no invented ones. No game mechanics as science analogies.

## 6. Materials and content constraints

- **Home use only.** Never for use outside the home.
- Sec 1 Science: *Lower Secondary Science (Sec 1)* (Marshall Cavendish, assessment book); *Science for Lower Secondary G2/G3 Revised Edition Textbook 1A* (CPDD/MOE); school class notes (*Integrated Science Notes 2026*, per chapter); multiple 2024 school exam papers. No Sec 2 materials bought yet; James plans to buy them in December and ingest in December–January.
- Only past-year papers can be used (no SEC papers). Up to about 20 papers per subject are bought for year-end exams; their marking keys are publisher-written, not official.
- Assessment books can be cut up for scanning; their answer keys are probably better.
- Scanner: ScanSnap iX1500, up to 600 ppi, colour pages scanned in colour. Current scans are 200 ppi image-only PDFs.
- Teacher-marked copies of her answers are not available.
- School materials are inconsistent: some modules come as PDFs, some as printed sheets, some as worksheets only, some are taught orally with nothing written. Topic lists for exams exist only for Geography. So the app cannot depend on school notes; they are an optional supplement.
- MOE syllabus documents, obtained 29 Sep and summarised in `Syllabus-Topic-Map.md` (versions match MOE's curriculum page): 2023 G2/G3 Lower Secondary Science (57 pages, file dated Jan 2026); 2021 Lower Secondary G2/G3 Geography (44 pages); 2021 Lower Secondary G2/G3 History (37 pages). Upper-secondary syllabuses (SEAB) wait until she chooses subjects at the end of Sec 2. The school adds topics outside the syllabus (e.g. Weather and Climate in Sec 1 Geography), so the topic tree must accept school-added topics as well as "not taught" ones.
- Scanning: double-sided scanning of cut-up books means chapter files share boundary pages; this is fine (see section 8).

## 7. Environment and constraints

- **Development:** Claude Code (or Claude Desktop) on James's Windows laptop, working in a GitHub repository; James commits with GitHub Desktop after every change.
- **Deployment:** clone on the Ubuntu home server, then pull changes. The repo must be ready to run in Docker before first run, served on **port 3002**. Remote access, if ever needed, via Tailscale (James will handle).
- **Server:** Ubuntu 26.04.1 LTS, Docker 29.8.1, Docker Compose v5.5.1, x86_64. Treated as a production server (it is also the backup server): nothing runs there except deployed containers.
- **Network:** no reverse proxy. Apps are reached over plain HTTP by port on the LAN, and remotely via Tailscale for authorised users only.
- **Compose conventions** (from James's Patchstash app): `build: .`; host port from `${PORT}` in `.env`; `env_file: .env`; data directory bind-mounted from `${DATA_DIR:-./data}`; `restart: unless-stopped`; an HTTP healthcheck.
- **Claude Code** runs in the Claude desktop app on the Windows laptop, confined to the repo folder with permission prompts on (the laptop is re-installable). Revisit running it on the server only if the laptop loop becomes a headache.
- **Laptop:** minimal installs; no local test environment. James deploys to the server to test each time.
- **Secrets:** the repo is private and holds code only. API keys, the database, scans and any deployment-specific Docker settings live only on the server and can be changed at deployment time.
- Stack: Python and SQLite.
- James: systems engineer/admin with 20+ years' experience; reads code fine, not a developer.
- Claude plan: Individual Pro (Claude Code included). Budget: under SG$100 per month total (Claude subscription plus API); exceeding it needs a very good reason.
- Target: something beneficial in service by February; January can be loose.

## 8. Working hypotheses (Claude's recommendations)

**Architecture**
- Put the workflow in code and use Claude only for bounded tasks (question generation, marking, explanation, concept Q&A, hints). All scoring is server-side, and the mark scheme never reaches her device before she answers.
- One app and one container for all subjects and levels, with subject and level as data fields and a per-question marking profile: point-marked, LORMS-source (History inference), LORMS-explain (History essays, Geography descriptive).
- For LORMS, Claude reports findings (e.g. "two inferences, one supported by source evidence") and code applies the key's award rules; borderline answers get the lower level with a flag and a reason.
- Confidence based mainly on mark-scheme source (assessment-book key high, publisher paper key medium, AI-generated lower), plus a borderline flag.
- Storage: notes as Markdown per module; questions, keys and attempts in SQLite. No vector database; load a whole module into the prompt with prompt caching.
- Token discipline: small bounded prompts, cached module text, a smaller model for hints and simple explanations, a larger one only for multi-mark and LORMS marking.

**Cumulative levels (proposal)**
- Topic tree per subject built from the MOE syllabus; every note, question and attempt tagged with subject, level (Sec 1–4) and topic. As far as I know, Lower Secondary Science is one syllabus spanning Sec 1–2, and upper-secondary science splits into different subjects, so subjects must be configurable rather than hard-coded (to confirm against the MOE documents).
- Session start offers: "This year" (default: completed topics of the current level), "Mix in earlier years" (completed topics across all levels) or hand-picked topics. When a school year ends, all its topics become taught by default.
- The spaced review draws from all taught topics across levels, weighted to weak ones; this is where Sec 1 retention happens.
- Dashboard filters by level and subject.
- "Builds on" links between topics across levels (accepted): Claude proposes the links once per syllabus from the topic tree, James skims them, and they are stored as data; a weak topic then suggests revision of the earlier topics it builds on.

**Ingestion**
- Image-only scans are fine; no text layer is needed. The pipeline renders pages on the server and reads them itself.
- Scanning: either whole books or chapter files, with answer keys as a separate file in both cases. There is no need to remove duplicate boundary pages; the pipeline splits content by chapter heading and question number and drops duplicates.
- Personal details: no manual covering needed. The pipeline skips cover pages and name boxes.
- Tiered by page type (tested on the 29 Sep samples): local OCR (Tesseract) for typed prose such as class notes and textbook text; Claude vision once for answer keys, exam papers, and any page with tables, diagrams or picture sources. The structured result is stored, so page images are never resent at marking time.
- Scan at 200 ppi for papers and keys, 300 ppi for textbooks and assessment books; keep colour; no conversion.
- Pipeline auto-corrects page orientation, skips cover pages carrying her name, and splits by question rather than by file, since chapter files overlap.
- Checks: question and answer counts match, marks add up, numbering continuous. Human review: answer keys closely at first, then spot-checks; errors later caught by the "looks wrong" button.
- Runs as a script on the server (where the scans and API key live), not interactively in Claude Code. Claude Code writes and debugs it.
- Calibration without teacher marks: run the marker on the keys' own pre-levelled sample answers; then aim for agreement within one mark or one level with James's own marking of 10–15 of her answers.

**Repo and deployment**
- Private GitHub repo containing code only. Scans, extracted content, the database and the API key live in a data directory and `.env` on the server, all excluded by `.gitignore`.
- `.gitattributes` forcing LF line endings, because Windows CRLF breaks shell scripts inside Linux containers.
- Dockerfile plus compose file mapping port 3002, with a named volume or bind mount for the data directory, and `.env.example`.
- Separate development database; never develop against her real history.
- Separate Anthropic API account for the app, with a monthly spend limit.

**Gamification**
- Modules as map regions ("explored" percentage doubles as coverage); a mastery ladder per topic topping out at consistent 75%+ first-attempt marks ("General", with Gorou's banner raised); a weekly-goal streak ("Will to Win") instead of a daily one; cosmetic rewards only; points for first-attempt marks plus a small capped rewrite bonus.
- Guide roles: Mitya explains concepts; Gorou coaches after marking; Paimon appears only when she presses "Ask Paimon" (hints); Vesna, Lohen and Chongyun as swappable voices. Badges named after KickFlip song titles.
- Flavour lines from a fixed hand-written bank James vetoes, shown roughly one response in five; no Claude tokens spent on them. All names and titles verified before shipping.

**Build order (confirmed):** skeleton (web app, schema, PIN) → marking loop on hand-entered questions → one-chapter ingestion pilot with review page → Sec 1 content → Sec 2 ingestion (Dec–Jan) → History and Geography LORMS → dashboard and theme. Live in February at the latest, aiming well ahead; James can put in many hours in November.

**Accelerated schedule (proposal):** October: marking prompts tested in chat on real samples; repo skeleton deployed; one-chapter ingestion pilot. November: all Sec 1 Science ingested; marking loop, student sessions and basic dashboard working; soft launch on Sec 1 Science by early December so it helps over the holidays. December–January: Sec 2 books ingested as bought; History and Geography marking; theme and badges. February: complete.

**Who does what:** chat in this Project for design, brief updates, testing marking prompts on real samples and reviewing ingestion output; Claude Code for all code, scripts and Docker files, in the repo folder; James reviews diffs, commits with GitHub Desktop, pulls and runs on the server, and reports errors back.

**Compact notes mode (accepted 29 Sep):** for a topic, the app shows highly compact bullet points (generated once per subtopic, reviewed, stored); she copies them by hand, then taps the words she thinks matter to underline them; code compares her choices with the topic's key-term list (the terms that earn marks in the answer keys); a guide explains missed terms and why they earn marks; she can ask questions. Shares the key-term list with marking, so it reinforces terminology. Proposed placement: after the marking loop, before the dashboard.

**Cost:** Pro subscription about SG$26/month; API estimate about US$5–20/month (unverified). Ingestion vision cost is small: roughly 1,600 input tokens per page, so a 37-page paper is about 60k tokens.

## 9. Open questions and outstanding work

**From James**
1. History and Geography calibration samples: only one exists (Bedok South 2023 History, Q1a–b with LORMS, marked by Claude on 29 Sep). Further samples come from her app answers once live, with James agreeing or disagreeing with each mark.
2. Setup tasks: see the October plan in the 29 Sep chat (laptop, API account, server folders and deploy key, scanning, asking her which chapters were taught, buying Sec 2 books early).

**Notes**
4. Her exams finish around 13 Oct; marked papers are rarely returned, so calibration relies on the answer keys' own example answers and James's quick judgement.

## 10. Decisions log

| Date | Decision | Made by |
|---|---|---|
| 2026-09-24 | Success target is about 45%→75% per assessment; good enough beats perfect | James |
| 2026-09-24 | Material is for use at home only | James |
| 2026-09-24 | Budget ceiling SG$100/month (subscription plus API) | James |
| 2026-09-24 | Host in containers on the home Linux server | James |
| 2026-09-24 | Parent dashboard needs no full authentication but must resist student tampering | James |
| Sep 2026 | Stack: Python and SQLite | James |
| Sep 2026 | Dashboard behind a PIN; stats reviewed together, not secret | James |
| Sep 2026 | She reports completed topics each session, parent can override, topics can be "not taught" | James |
| Sep 2026 | Question bank: mix of real and generated | James |
| Sep 2026 | Something beneficial live by February; January loose | James |
| Sep 2026 | Gamification themed on Genshin Impact and K-pop, light touch; no lyrics | James |
| Sep 2026 | Accepted: rewrite loop, question-reading hints, time-of-day view | James |
| Sep 2026 | Rejected: visible pace clock (pace in stats only) | James |
| Sep 2026 | "Claude-light" means keeping token counts low, not avoiding Claude | James |
| Sep 2026 | Rewrite prompted after lost marks, with skip button | James |
| Sep 2026 | First-attempt marks drive stats and points | James |
| Sep 2026 | Badge and title key in the parent section | James |
| Sep 2026 | Marking shows a confidence level; spaced review is skippable | James |
| Sep 2026 | Individual Pro plan | James |
| 2026-09-29 | Develop on Windows laptop in a GitHub repo (GitHub Desktop); deploy by clone/pull to Ubuntu server, Docker, port 3002 | James |
| 2026-09-29 | App holds cumulative Sec 1–4 material and tests across years | James |
| 2026-09-29 | App name: Teeyooshun; private GitHub repo created | James |
| 2026-09-29 | Accepted: "builds on" links across levels; logging rewrite skips on the dashboard | James |
| 2026-09-29 | "We weren't taught this" flag on questions and topics, visible to parents | James |
| 2026-09-29 | Page orientation fixed by the pipeline, not by hand | James |
| 2026-09-29 | Secrets and deployment-specific Docker settings stay out of the repo | James |
| 2026-09-29 | No local test environment on the laptop; test by deploying to the server | James |
| 2026-09-29 | Build order confirmed; February go-live at the latest, aim well ahead | James |
| 2026-09-29 | Claude Code runs on the laptop, not the server; server treated as production | James |
| 2026-09-29 | Upper-secondary syllabuses deferred until subject choice at end of Sec 2 | James |
| 2026-09-29 | Speed up October | James |
| 2026-09-29 | Accepted: compact notes mode | James |
| 2026-09-29 | No reverse proxy; LAN HTTP by port plus Tailscale | James |
