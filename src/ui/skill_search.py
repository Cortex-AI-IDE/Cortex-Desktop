"""Ranked keyword search over the skill library.

With 251 skills the old search, a raw substring test over
`name + description`, leaving cards in alphabetical order, stopped working:

  * "3d website" matched NOTHING. The skill is `3d-website-architect`, and a
    space is not a hyphen.
  * "seo audit" matched nothing for the same reason.
  * "responsive" found the right skill but left it 40 rows down, because
    matches were only hidden, never reordered.
  * tags were never searched at all.

This module fixes all four: separators are equivalent, every term must match
(AND), matches are RANKED, and tags count. Pure functions, no Qt, so both
the Skills browser and the "/" menu can share one behaviour.
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional

# Where a term matched, best first. Name beats tag beats description: typing
# "seo" should surface `seo` itself before a frontend skill that merely
# mentions SEO in prose.
_EXACT_NAME = 1000
_NAME_PREFIX = 500
_NAME_WORD = 300
_NAME_SUB = 150
_TAG_EXACT = 120
_TAG_SUB = 60
_DESC_WORD = 40
_DESC_SUB = 10

# Matching the WHOLE query as a phrase in the name outranks any per-term
# total. Without this, "seo audit" ranked plain `seo` first: its exact-name
# hit (1000) plus "audit" appearing in its description beat `seo-audit`
# matching BOTH words in its name. Caught against the real 251-skill library.
_PHRASE_EXACT = 5000
_PHRASE_PREFIX = 2000
_PHRASE_SUB = 800

_SEP = re.compile(r"[\s\-_/.,:]+")


def normalize(text: str) -> str:
    """Lowercase, and collapse every separator to a single space.

    This is the whole reason "3d website" now finds `3d-website-architect`:
    both sides become "3d website ...".
    """
    return _SEP.sub(" ", (text or "").lower()).strip()


def terms(query: str) -> List[str]:
    return [t for t in normalize(query).split(" ") if t]


def _word_match(haystack: str, term: str) -> bool:
    """True when the term starts a word, 'audit' matches 'seo audit' but
    'dit' does not, so partial noise ranks below real hits."""
    return haystack == term or haystack.startswith(term + " ") or \
        (" " + term) in haystack


def _score_term(term: str, name: str, tags: str, desc: str) -> int:
    if name == term:
        return _EXACT_NAME
    if name.startswith(term):
        return _NAME_PREFIX
    if _word_match(name, term):
        return _NAME_WORD
    if term in name:
        return _NAME_SUB
    if tags:
        if _word_match(tags, term):
            return _TAG_EXACT
        if term in tags:
            return _TAG_SUB
    if _word_match(desc, term):
        return _DESC_WORD
    if term in desc:
        return _DESC_SUB
    return 0


def score(skill: Dict, query: str) -> Optional[int]:
    """Total score, or None when the skill does not match.

    EVERY term must match somewhere. Typing more words has to narrow the
    list, with 251 skills an OR search returns almost everything and is
    worse than no search at all.
    """
    qs = terms(query)
    if not qs:
        return 0
    name = normalize(skill.get("name", ""))
    desc = normalize(skill.get("description", ""))
    raw_tags = skill.get("tags") or []
    tags = normalize(" ".join(raw_tags) if isinstance(raw_tags, list)
                     else str(raw_tags))

    total = 0
    for t in qs:
        s = _score_term(t, name, tags, desc)
        if s == 0:
            return None
        total += s

    # Whole-query phrase bonus, see the constants above for why this has to
    # outrank the per-term total.
    phrase = " ".join(qs)
    if name == phrase:
        total += _PHRASE_EXACT
    elif name.startswith(phrase):
        total += _PHRASE_PREFIX
    elif phrase in name:
        total += _PHRASE_SUB
    return total


def search(skills: List[Dict], query: str,
           limit: Optional[int] = None) -> List[Dict]:
    """Skills matching `query`, best first. Empty query -> alphabetical."""
    if not terms(query):
        out = sorted(skills, key=lambda s: s.get("name", ""))
        return out[:limit] if limit else out

    scored = []
    for s in skills:
        sc = score(s, query)
        if sc is not None:
            scored.append((-sc, s.get("name", ""), s))
    scored.sort(key=lambda t: (t[0], t[1]))     # score desc, then name
    out = [s for _, _, s in scored]
    return out[:limit] if limit else out


def matching_names(skills: List[Dict], query: str) -> List[str]:
    """Ranked names only, what a widget needs to show and reorder."""
    return [s.get("name", "") for s in search(skills, query)]


# ── Prompt-time routing (different problem to the menu) ──────────────────
# The "/" menu takes a deliberate 1-3 word query and AND-matches it: typing
# more words must narrow 251 skills down. The skill router in the system
# prompt gets the user's whole sentence instead ("make the landing page
# animations feel smoother"), where AND-matching returns nothing at all -
# "make", "the" and "feel" match no skill, so one useless term kills the
# whole query. Routing therefore scores per term and SUMS, requiring only
# that something matched.

_STOPWORDS = frozenset("""
a an and are as at be been but by can could do does doing for from get got
had has have how i if in into is it its just like make makes me my need
needs of on or our out please should so some that the their them then there
these they this to up us use used using want was we were what when where
which who why will with would you your
""".split())


def rank_for_prompt(skills: List[Dict], text: str,
                    limit: int = 30) -> List[Dict]:
    """Skills most plausibly relevant to a free-text request, best first.

    Unlike search(), a term that matches nothing is ignored rather than
    disqualifying the skill, and stopwords are dropped so ordinary English
    does not drown the signal. Returns [] when nothing matched at all, so the
    caller can fall back instead of injecting noise.
    """
    qs = [t for t in terms(text) if t not in _STOPWORDS and len(t) > 2]
    if not qs:
        return []

    # Pre-normalise once, then weight each term by how RARE it is. Without
    # this, "build a pdf report" returned build-isometric-arpg,
    # build-threejs-enemy-systems, build-game-audio... because "build" is a
    # name prefix on dozens of skills and swamped the one term that mattered.
    # A term matching half the library carries almost no signal; one matching
    # three skills carries most of it.
    import math
    prepped = []
    for s in skills:
        raw_tags = s.get("tags") or []
        prepped.append((
            s,
            normalize(s.get("name", "")),
            normalize(" ".join(raw_tags) if isinstance(raw_tags, list)
                      else str(raw_tags)),
            normalize(s.get("description", "")),
        ))

    df = {}
    for t in qs:
        df[t] = sum(1 for _, n, tg, d in prepped if _score_term(t, n, tg, d))
    n_docs = max(1, len(prepped))
    idf = {t: math.log(1 + n_docs / (1 + c)) for t, c in df.items()}

    scored = []
    for s, name, tags, desc in prepped:
        total = 0.0
        hits = 0
        coverage = 0.0
        for t in qs:
            sc = _score_term(t, name, tags, desc)
            if sc:
                total += sc * idf[t]
                hits += 1
                coverage += idf[t]
        if hits:
            # Covering MORE of the request should rank higher - but weighted,
            # not counted. A flat per-hit bonus let a skill matching two
            # throwaway words outrank one matching the single word that
            # carried the whole request.
            scored.append((-(total + coverage * 200), s.get("name", ""), s))
    scored.sort(key=lambda t: (t[0], t[1]))
    return [s for _, _, s in scored[:limit]]
