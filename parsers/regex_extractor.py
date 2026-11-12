"""
Bilingual (English/French) field extractor for Quebec CEGEP academic disclosures.

Design rules (from PRD):
- Permissive: capture any numeric value near a keyword; never discard based on
  physical plausibility bounds — that is downstream work.
- Always return raw_text intact; callers decide what to store.
- Return None for any field that could not be extracted.
"""

import re
from typing import Optional

# ---------------------------------------------------------------------------
# R-Score / Cote R / CRC
# Forward:  "cote r 32.5", "r-score: 28", "CRC de 31", "mon crc est 29,8"
# Inverted: "35.2 cote r", "34.5 r-score", "33 crc"
#
# Two named capture groups so _rscore_float() can pick whichever branch fired:
#   fwd — standard keyword-first form
#   inv — inverted number-first form
#
# \b before the inverted number prevents matching a trailing fragment of a
# larger integer (e.g. "101 cote r" cannot match as "01 cote r").
# ---------------------------------------------------------------------------
_RSCORE_KEYWORDS = r"""(?:
  cote\s+de\s+rendement\s+collégial?  # full French label (longest — must be first)
  | r[\s\-]?score                      # "r-score", "r score", "rscore"
  | cote[\s\-]?r                       # "cote r", "cote-r"
  | \bcrc\b                            # abbreviation
)"""

RE_RSCORE = re.compile(
    r"""
    (?:
      # ── Forward: keyword → number ───────────────────────────────────────
      """ + _RSCORE_KEYWORDS + r"""
      (?:\s*[:=,]\s*|\s+(?:of|de|à|du|d['']|est|is|était|around|environ)\s+|\s+)
      (?P<fwd>\d{1,2}(?:[.,]\d{1,2})?)
    |
      # ── Inverted: number → keyword ──────────────────────────────────────
      \b(?P<inv>\d{1,2}(?:[.,]\d{1,2})?)
      \s+
      """ + _RSCORE_KEYWORDS + r"""
    )
    """,
    re.IGNORECASE | re.VERBOSE,
)


def _rscore_float(match) -> Optional[float]:
    """Return the numeric value from whichever RE_RSCORE branch fired."""
    if match is None:
        return None
    try:
        val = match.group("fwd") or match.group("inv")
        return float(val.replace(",", ".")) if val else None
    except (ValueError, AttributeError):
        return None

# ---------------------------------------------------------------------------
# Class average / Moyenne de groupe
# Matches: "class avg 72%", "class average: 68.5", "moyenne du cours 71"
# Note: "avg" and "average" are spelled differently so both are listed
#       explicitly — "avg(?:erage)?" does NOT match "average".
# ---------------------------------------------------------------------------
RE_CLASS_AVG = re.compile(
    r"""
    (?:
      class\s+(?:avg(?:erage)?|average)
      | group\s+(?:avg(?:erage)?|average)
      | moyenne\s+(?:de\s+)?(?:groupe|classe|du\s+cours?|de\s+la\s+classe)
      | moyenne\s+générale
      | avg(?:erage)?\s+of\s+the\s+class
      | \bmoyenne\b(?!\s+(?:cumulative|pondérée))  # bare "moyenne" but not GPA
    )
    (?:\s*[:=,]\s*|\s+(?:of|de|à|du|était|is|est|was)?\s*|\s+)
    (\d{1,3}(?:[.,]\d{1,2})?)
    \s*%?
    """,
    re.IGNORECASE | re.VERBOSE,
)

# ---------------------------------------------------------------------------
# Individual grade
# Matches: "got 85%", "grade: 78", "j'ai eu 92%", "note de 88", "obtenu 76"
#
# Key guard: "scored?" would match "Score" inside "R-Score: 34" because
# the hyphen in "R-Score" creates a \b word boundary before "Score".
# The negative lookbehind (?<![A-Za-z\-]) prevents that false positive.
# ---------------------------------------------------------------------------
RE_GRADE = re.compile(
    r"""
    (?:
      my\s+grade
      | \bgrade[d]?\b
      | (?<![A-Za-z\-])scor(?:ed?|ing)   # "scored/score/scoring" but NOT "R-Score"
      | \bmark\b
      | note\s+(?:de\s+|finale\s*|du\s+cours?\s*)?
      | j['']ai\s+(?:eu|obtenu)\s+(?:un[e]?\s+)?
      | \bobtenu?\b
      | résultat
      | j['']ai\s+fini\s+avec
      | \bgot\b
    )
    # \s* (zero-or-more) is the final fallback so French patterns like
    # "j'ai eu 76%" work even when the keyword already consumed the space.
    (?:\s*[:=,]\s*|\s+(?:of|de|à|un[e]?)?\s*|\s*)
    (\d{1,3}(?:[.,]\d{1,2})?)
    \s*%?
    """,
    re.IGNORECASE | re.VERBOSE,
)

# ---------------------------------------------------------------------------
# Standard deviation
# Matches: "std dev 8.5", "écart-type 11", "σ = 9", "standard deviation: 12.3"
# ---------------------------------------------------------------------------
RE_STD_DEV = re.compile(
    r"""
    (?:
      std(?:\.?\s*dev(?:iation)?)   # "std dev", "std.dev", "std deviation"
      | standard\s+deviation         # "standard deviation"
      | (?:é|e)cart[\s\-]type       # "écart-type", "ecart-type", "écart type"
      | \bσ\b                        # Greek sigma symbol
      | \bsigma\b                    # written out
    )
    (?:\s*[:=,]\s*|\s+(?:of|de|à|est|is|was)?\s*|\s+)
    (\d{1,2}(?:[.,]\d{1,2})?)
    \s*%?
    """,
    re.IGNORECASE | re.VERBOSE,
)

# ---------------------------------------------------------------------------
# Course credits / unités
# Matches: "3 credits", "2 unités", "worth 3 cr.", "credits: 2"
# Named groups: a = keyword-first ("credits: 3"), b = number-first ("3 credits")
# ---------------------------------------------------------------------------
RE_CREDITS = re.compile(
    r"""
    (?:
      # keyword → number: "credits: 3", "unités = 2"
      (?:cr(?:éd?its?|edits?|\.)?|unités?|units?)
      (?:\s*[:=,]\s*|\s+)
      (?P<a>\d{1,2})\b
    |
      # number → keyword: "3 credits", "2 unités"
      \b(?P<b>\d{1,2})
      \s+
      (?:cr(?:éd?its?|edits?|\.)?|unités?|units?)\b
    )
    """,
    re.IGNORECASE | re.VERBOSE,
)


def _credits_float(match) -> Optional[float]:
    if match is None:
        return None
    try:
        val = match.group("a") or match.group("b")
        return float(val) if val else None
    except (IndexError, ValueError, AttributeError):
        return None


# ---------------------------------------------------------------------------
# High school / école secondaire
# Matches generic keywords ("école secondaire X", "polyvalente X", "high school X")
# and a named list of prominent Quebec private secondary schools.
# ---------------------------------------------------------------------------
_HIGH_SCHOOLS = [
    # Mentioned explicitly by project spec
    "Brébeuf", "Brebeuf",           # Collège Jean-de-Brébeuf (sec + cégep)
    "André-Grasset", "Grasset",
    "Jean-Eudes",
    # Other prominent Quebec private secondary schools
    "Stanislas",
    "Regina Assumpta",
    "Père-Marquette",
    "Marie de France",
    "Pensionnat du Saint-Nom-de-Marie",
    "Loyola High",
    # English private
    "Lower Canada College",
    "Selwyn House",
    "Villa Maria",
    "Sacred Heart",
    "Marymount",
    "The Study",
    "Miss Edgar",
]
_HIGH_SCHOOLS_SORTED = sorted(_HIGH_SCHOOLS, key=len, reverse=True)

# Named schools take priority over keyword capture so multi-word names like
# "Lower Canada College" aren't truncated by the keyword pattern.
RE_HIGH_SCHOOL_NAMED = re.compile(
    r"\b(" + "|".join(re.escape(s) for s in _HIGH_SCHOOLS_SORTED) + r")\b",
    re.IGNORECASE,
)

# Keyword pattern captures the FIRST (possibly hyphenated) word after the
# prefix to avoid greedily swallowing trailing sentence words.
RE_HIGH_SCHOOL_KEYWORD = re.compile(
    r"""
    (?:école\s+secondaire|polyvalente|high\s+school|secondary\s+school|lycée)
    \s+
    ([A-Za-zÀ-ÿ][A-Za-zÀ-ÿ\'\-]{2,30})
    """,
    re.IGNORECASE | re.VERBOSE,
)


def _high_school_str(text: str) -> Optional[str]:
    """Named-list lookup first; keyword extraction as fallback."""
    m = RE_HIGH_SCHOOL_NAMED.search(text)
    if m:
        return m.group(1).strip()
    m = RE_HIGH_SCHOOL_KEYWORD.search(text)
    if m:
        return m.group(1).strip()
    return None


# ---------------------------------------------------------------------------
# Course codes
# Matches: NYA, NYB, NYC (CEGEP science sequences) + standard codes like BIO-401
# ---------------------------------------------------------------------------
RE_COURSE = re.compile(
    r"""
    \b(
      NY[ABC]                                        # CEGEP sequences
      | [A-Z]{2,5}[\s\-]?\d{3}(?:[\-\.]\d{2,3})?   # e.g. BIO-401.A1, CHEM 202
    )\b
    """,
    re.IGNORECASE | re.VERBOSE,
)

# ---------------------------------------------------------------------------
# CEGEP / institution name
# ---------------------------------------------------------------------------
_CEGEPS = [
    # Private
    "Marianopolis", "Brébeuf", "Brebeuf",
    # English-language public
    "Dawson", "Vanier", "John Abbott", "JohnAbbott", "Champlain",
    # French-language public — Montreal
    "Ahuntsic", "André-Laurendeau", "Lionel-Groulx", "Maisonneuve",
    "Montmorency", "Rosemont", "Saint-Laurent", "Vieux Montréal",
    # Elsewhere in Quebec
    "Limoilou", "Sainte-Foy", "Trois-Rivières", "Sherbrooke",
    "Jonquière", "Chicoutimi", "Rimouski", "Outaouais",
    "Granby", "Drummondville", "Sorel-Tracy", "St-Jean",
    "St-Hyacinthe", "Sept-Îles", "La Pocatière", "Lanaudière",
    "Rivière-du-Loup", "Thetford", "Rouyn-Noranda",
]
# Sort longest first so overlapping names (e.g. "John Abbott" vs "John") match correctly
_CEGEPS_SORTED = sorted(_CEGEPS, key=len, reverse=True)

RE_CEGEP = re.compile(
    r"\b(" + "|".join(re.escape(c) for c in _CEGEPS_SORTED) + r")\b",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def _to_float(match) -> Optional[float]:
    if match is None:
        return None
    try:
        return float(match.group(1).replace(",", "."))
    except (ValueError, IndexError, AttributeError):
        return None


def extract_fields(text: str) -> dict:
    """
    Extract all candidate fields from raw text.

    Never raises, never discards the caller's record — missing fields are None.
    Numeric fields are returned as float so they map directly to REAL columns.
    """
    if not text:
        return {
            "r_score": None, "class_average": None, "grade": None,
            "course": None, "cegep": None, "std_dev": None,
            "credits": None, "high_school": None,
        }

    course_m = RE_COURSE.search(text)
    cegep_m  = RE_CEGEP.search(text)

    return {
        "r_score":       _rscore_float(RE_RSCORE.search(text)),
        "class_average": _to_float(RE_CLASS_AVG.search(text)),
        "grade":         _to_float(RE_GRADE.search(text)),
        "course":        course_m.group(1).upper() if course_m else None,
        "cegep":         cegep_m.group(1)          if cegep_m  else None,
        "std_dev":       _to_float(RE_STD_DEV.search(text)),
        "credits":       _credits_float(RE_CREDITS.search(text)),
        "high_school":   _high_school_str(text),
    }
