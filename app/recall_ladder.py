"""The scaffolded recall ladder: which format a card is recalled in, and the code-only parts of each format.

Box 1 word bank, box 2 fill the gaps, box 3 starters, boxes 4-5 free recall with slots. Image cards stay self-tick.
Only starters and free recall use the model (app.revise.typed_check); everything here is plain code, no web and
no API. Revise sets and session steps share it, because both are Revise sets.

A card's points are its check units (app.revise.check_units): {"id", "text", "detail", "key_terms", "any_of"}.
"""

import random
import re
import zlib

WORDBANK, GAPS, STARTERS, FREE, MATCH, PAPER = "wordbank", "gaps", "starters", "free", "match", "paper"
FORMATS = (WORDBANK, GAPS, STARTERS, FREE, MATCH)
LABELS = {WORDBANK: "Word bank", GAPS: "Fill the gaps", STARTERS: "Starters", FREE: "Free recall",
          MATCH: "Matching", PAPER: "Self-tick"}

# The highest box a pass in each format can take a card to (a pass in a lower-capped format never lowers a card).
FORMAT_MAX_BOX = {WORDBANK: 2, GAPS: 3, MATCH: 3, STARTERS: 5, FREE: 5}

MAX_BLANKS = 3          # most blanks in one point
DISTRACTORS = 3         # most wrong words added to a word bank
START_EXTRA_WORDS = 4   # words "Give me a start" reveals
STARTER_MIN, STARTER_MAX, STARTER_DEFAULT = 3, 6, 5

STOPWORDS = frozenset("""
a an the and or but nor so yet of in on at to for from by with without into onto over under above below between
among through during before after about against as is are was were be been being am do does did done has have had
having it its it's this that these those there here they them their he she his her we us our you your i me my
which who whom whose what when where why how not no than then too very can could may might must shall should
will would also just only each every any all some such more most other another both either neither one two
if because while although though until unless whether once up down out off again further
""".split())


# ---- Which format ---------------------------------------------------------------------------------

def choose_format(card: dict, box: int, units: list, *, low_energy: bool = False, long_limit: int = 4,
                  self_tick: bool = False) -> str:
    """The format for a card in this box.

    self_tick: an image card or a Quick-fire set (unchanged: she ticks it herself).
    Low energy uses only the easy formats whatever the box. A card with no blankable point goes to starters
    instead of word bank or fill the gaps. A card with more than long_limit points never goes beyond starters.
    """
    if self_tick or not units:
        return PAPER
    if low_energy:
        wanted = WORDBANK if box <= 1 else GAPS
    elif box <= 1:
        wanted = WORDBANK
    elif box == 2:
        wanted = GAPS
    elif box == 3:
        wanted = STARTERS
    else:
        wanted = STARTERS if len(units) > long_limit else FREE
    if wanted in (WORDBANK, GAPS) and not any(blank_unit(u) for u in units):
        return STARTERS
    return wanted


# ---- Blanks ------------------------------------------------------------------------------------------

def _term_span(text: str, term: str):
    term = (term or "").strip()
    if not term:
        return None
    m = re.search(r"(?<!\w)" + re.escape(term) + r"(?!\w)", text, re.IGNORECASE)
    return m.span() if m else None


def _words(text: str) -> list[tuple[int, int, str]]:
    """Words of the text (letters, with inner hyphens and apostrophes; numbers are not words) as (start, end, lower)."""
    return [(m.start(), m.end(), m.group().lower()) for m in re.finditer(r"[A-Za-z][A-Za-z'\-]*[A-Za-z]|[A-Za-z]", text)]


def _fallback_span(text: str):
    """The most specific noun phrase of a point that has no usable highlighted term: the longest content word with
    up to two neighbouring content words, never the first word of the sentence. A point under four words blanks its
    last content word instead. A one-word point is not blanked (nothing would be left to read). None if no luck."""
    words = _words(text)
    if len(words) < 2:
        return None
    content = [i for i, w in enumerate(words) if w[2] not in STOPWORDS]
    if len(words) < 4:
        return (words[content[-1]][0], words[content[-1]][1]) if content else None
    pool = [i for i in content if i != 0] or content
    if not pool:
        return None
    best = max(pool, key=lambda i: (len(words[i][2]), i))   # longest; a tie goes to the later word
    lo = hi = best

    def joins(a, b, new):   # a content word right next to the phrase, with nothing but spaces between
        return text[words[a][1]:words[b][0]].strip() == "" and words[new][2] not in STOPWORDS

    while hi - lo + 1 < 3:
        if hi + 1 < len(words) and joins(hi, hi + 1, hi + 1):
            hi += 1
        elif lo - 1 > 0 and joins(lo - 1, lo, lo - 1):
            lo -= 1
        else:
            break
    return words[lo][0], words[hi][1]


def blank_spans(unit: dict) -> list[tuple[int, int]]:
    """Character spans of the unit's text to blank, in text order (at most MAX_BLANKS).

    Its highlighted terms first (the first occurrence of each); with none found, the fallback phrase."""
    text = unit.get("text", "")
    spans = []
    for term in unit.get("key_terms") or []:
        span = _term_span(text, term)
        if span and not any(span[0] < e and s < span[1] for s, e in spans):
            spans.append(span)
        if len(spans) == MAX_BLANKS:
            break
    if not spans:
        fallback = _fallback_span(text)
        spans = [fallback] if fallback else []
    return sorted(spans)


def blank_unit(unit: dict):
    """The point with blanks: {"segments": [{"text"} | {"blank": n}], "answers": [str], "alternatives": [[str]]},
    or None if nothing in it can be blanked. alternatives[n] are other accepted words for blank n (a point's
    optional {term: [alternatives]} map)."""
    spans = blank_spans(unit)
    if not spans:
        return None
    text, segments, answers, pos = unit["text"], [], [], 0
    for n, (s, e) in enumerate(spans):
        if s > pos:
            segments.append({"text": text[pos:s]})
        segments.append({"blank": n})
        answers.append(text[s:e])
        pos = e
    if pos < len(text):
        segments.append({"text": text[pos:]})
    alts = {_norm(k): v for k, v in (unit.get("alternatives") or {}).items()}
    return {"segments": segments, "answers": answers,
            "alternatives": [list(alts.get(_norm(a), [])) for a in answers]}


# ---- Checking typed or tapped words (code only) -------------------------------------------------------

def _norm(text: str) -> str:
    text = re.sub(r"[-_/]", " ", (text or "").lower())
    return " ".join(re.sub(r"[^0-9a-z ]", "", text).split())


def _within_one(a: str, b: str) -> bool:
    """One slip apart: a letter missing, extra or wrong, or two neighbouring letters swapped."""
    if a == b:
        return True
    if abs(len(a) - len(b)) > 1:
        return False
    if len(a) == len(b):
        diff = [i for i in range(len(a)) if a[i] != b[i]]
        if len(diff) == 1:
            return True
        return len(diff) == 2 and diff[1] == diff[0] + 1 and a[diff[0]] == b[diff[1]] and a[diff[1]] == b[diff[0]]
    short, long = (a, b) if len(a) < len(b) else (b, a)
    i = 0
    while i < len(short) and short[i] == long[i]:
        i += 1
    return short[i:] == long[i + 1:]


def _same_words(given: str, target: str, tolerant: bool) -> bool:
    if not given or not target:
        return False
    if given == target or given.replace(" ", "") == target.replace(" ", ""):
        return True
    if not tolerant:
        return False
    g, t = given.split(), target.split()
    # A slip is forgiven only in a word of 5 or more letters (the card's word), so short words stay exact.
    return len(g) == len(t) and all(a == b or (len(b) >= 5 and _within_one(a, b)) for a, b in zip(g, t))


def answer_matches(given: str, answer: str, alternatives=(), tolerant: bool = True) -> bool:
    """Does what she typed (or tapped) match the card's word? Case, punctuation and hyphens don't matter; with
    `tolerant`, one small spelling slip in a word of 5+ letters is accepted. Listed alternatives are accepted too."""
    g = _norm(given)
    return any(_same_words(g, _norm(t), tolerant) for t in [answer, *alternatives])


def score_blanks(given: list, blanked: dict, tolerant: bool = True) -> dict:
    """Score one point's blanks. Every blank right is 1, at least half right is 0.5, otherwise 0.
    Returns {"results": [bool per blank], "score": float}."""
    answers, alts = blanked["answers"], blanked["alternatives"]
    given = list(given) + [""] * (len(answers) - len(given))
    results = [answer_matches(given[n], answers[n], alts[n], tolerant) for n in range(len(answers))]
    right = sum(results)
    score = 1.0 if right == len(answers) else 0.5 if right * 2 >= len(answers) else 0.0
    return {"results": results, "score": score}


def filled_text(unit: dict, blanked: dict) -> str:
    """The point as the card words it (the blanks filled in), for the feedback after she has answered."""
    return "".join(seg["text"] if "text" in seg else blanked["answers"][seg["blank"]] for seg in blanked["segments"])


# ---- Word bank -----------------------------------------------------------------------------------------

def stable_seed(*parts) -> int:
    """The same seed for the same set, card and point, so a reload shows the bank in the same order."""
    return zlib.crc32(":".join(str(p) for p in parts).encode("utf-8"))


def distractor_pool(conn, card: dict) -> list[str]:
    """Highlighted terms of the other cards in the same topic: wrong words that still look like they belong."""
    import json

    from app.revise import check_units   # here, not at the top: revise will import this module

    pool = []
    for row in conn.execute("SELECT record_json FROM revision_cards WHERE topic = ? AND id != ? ORDER BY id",
                            (card["topic"], card["id"])):
        for unit in check_units(json.loads(row["record_json"])):
            pool += [t for t in unit["key_terms"] if t.strip()]
    return list(dict.fromkeys(pool))


def build_bank(answers: list, pool: list, unit_text: str, seed: int, count: int = DISTRACTORS) -> list[str]:
    """The words she taps from: the blanked words plus up to `count` wrong ones from `pool`, shuffled.

    A wrong word is never one of the answers, never contains or sits inside one, and never appears in the point's
    own text, so the right word can't be duplicated or given away."""
    rng = random.Random(seed)
    answer_norms = [_norm(a) for a in answers]
    text_norm = " " + _norm(unit_text) + " "
    usable, seen = [], set()
    for word in pool:
        n = _norm(word)
        if (not n or n in seen or f" {n} " in text_norm
                or any(n == a or n in a or a in n for a in answer_norms)):
            continue
        seen.add(n)
        usable.append(word)
    bank = list(answers) + rng.sample(usable, min(count, len(usable)))
    rng.shuffle(bank)
    return bank


# ---- Starters and "Give me a start" ---------------------------------------------------------------------

def starter_split(unit: dict) -> tuple[str, str]:
    """(opening words, the rest) of a point. The opening is the words up to its first highlighted term, at least 3
    and at most 6 words (5 with no term); a one-word point has no opening."""
    text = unit.get("text", "")
    words = text.split()
    if len(words) < 2:
        return "", " ".join(words)
    starts = [span[0] for span in (_term_span(text, t) for t in unit.get("key_terms") or []) if span]
    n = len(text[:min(starts)].split()) if starts else STARTER_DEFAULT
    n = max(STARTER_MIN, min(STARTER_MAX, n))
    n = min(n, len(words) - 1)
    return " ".join(words[:n]), " ".join(words[n:])


def reveal_start(unit: dict, shown_words: int) -> str:
    """"Give me a start": the point's opening words extended by START_EXTRA_WORDS (never the whole point)."""
    words = unit.get("text", "").split()
    upto = min(shown_words + START_EXTRA_WORDS, max(len(words) - 1, 1))
    return " ".join(words[:upto])


def cap_score(score: float, start_used: bool) -> float:
    """A point she took a start for scores at most half."""
    return min(score, 0.5) if start_used else score
