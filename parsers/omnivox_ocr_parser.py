"""
Omnivox / Léa transcript OCR parser.

Parses raw text extracted from Omnivox screenshot slides.

Primary path — course-code line parser
  Each line that contains a course code is treated as one academic row.
  Numbers following the code are assigned positionally with range guards:
    1. credits       — integer in [1, 9]  (optional; skipped if absent)
    2. grade         — float  in [0, 100]
    3. class_average — float  in [0, 100]
    4. std_dev       — float  in [0, 30]  (optional; only consumed when a
                                           subsequent r_score number exists)
    5. r_score       — float  in [5, 50]

  std_dev is treated as optional because Omnivox allows teachers to hide
  it from the student portal.  The lookahead rule means a 4-number row
  (credits, grade, avg, r_score) correctly yields r_score rather than
  misclassifying r_score as std_dev.

Mobile / cropped fallback — numberline parser
  Lines with no course code are tried against a compact layout regex seen
  in cropped TikTok screenshots:
      grade  average  r_score  credits
  Example: "91 76 34.815 2,66 A-202"
  Matched rows are returned with course_code=None and std_dev=None.
"""

import re

# Matches standard hyphenated form (601-101-MQ) and OCR variants with spaces
# or minor noise (601 101 MQ, 201-NYA-O5).  [-\s]{0,2} tolerates 0-2
# space/hyphen characters between segments.
_RE_COURSE_CODE = re.compile(
    r"\b(\d{3}[-\s]{0,2}[A-Z0-9]{3}[-\s]{0,2}[A-Z0-9]{2})\b",
    re.IGNORECASE,
)

_BOUNDS = {
    "credits":       (1.0,  9.0),
    "grade":         (0.0, 100.0),
    "class_average": (0.0, 100.0),
    "std_dev":       (0.0,  30.0),
    "r_score":       (5.0,  50.0),
}


def _extract_numbers(text: str) -> list[float]:
    nums: list[float] = []
    for tok in re.findall(r"\d+(?:[.,]\d+)?", text):
        try:
            nums.append(float(tok.replace(",", ".")))
        except ValueError:
            pass
    return nums


def _in_bounds(val: float, lo: float, hi: float) -> bool:
    return lo <= val <= hi


def _assign_fields(nums: list[float]) -> dict:
    row: dict = {k: None for k in _BOUNDS}
    idx = 0
    n = len(nums)

    # credits: must be an integer (no fractional part) in [1, 9]
    if idx < n and _in_bounds(nums[idx], *_BOUNDS["credits"]) and nums[idx] == int(nums[idx]):
        row["credits"] = nums[idx]
        idx += 1

    for field in ("grade", "class_average"):
        if idx < n and _in_bounds(nums[idx], *_BOUNDS[field]):
            row[field] = nums[idx]
            idx += 1

    # std_dev is optional: only consume it when at least one more number
    # follows (the r_score slot).  Without this guard, a 4-number row
    # (credits, grade, avg, r_score) would incorrectly assign r_score to
    # std_dev and leave r_score as None.
    if idx + 1 < n and _in_bounds(nums[idx], *_BOUNDS["std_dev"]):
        row["std_dev"] = nums[idx]
        idx += 1

    if idx < n and _in_bounds(nums[idx], *_BOUNDS["r_score"]):
        row["r_score"] = nums[idx]

    return row


# Cropped mobile layout: grade  average  r_score  credits  (no course code or std_dev)
# Example: "91 76 34.815 2,66 A-202"
_RE_MOBILE = re.compile(
    r"(?P<grade>\d{2,3})\s+(?P<average>\d{2,3})\s+(?P<r_score>\d{2}[.,]\d+)\s+(?P<credits>\d[.,]\d+)",
)


def _parse_mobile_fallback(line: str) -> dict | None:
    """Try the compact mobile layout on a line that has no course code."""
    m = _RE_MOBILE.search(line)
    if not m:
        return None
    try:
        grade   = float(m.group("grade"))
        average = float(m.group("average"))
        r_score = float(m.group("r_score").replace(",", "."))
        credits = float(m.group("credits").replace(",", "."))
    except ValueError:
        return None
    if not (_in_bounds(grade, *_BOUNDS["grade"])
            and _in_bounds(average, *_BOUNDS["class_average"])
            and _in_bounds(r_score, *_BOUNDS["r_score"])):
        return None
    return {
        "course_code":   None,
        "credits":       credits,
        "grade":         grade,
        "class_average": average,
        "std_dev":       None,
        "r_score":       r_score,
    }


def parse_omnivox_table(raw_ocr_text: str) -> list[dict]:
    """
    Return one dict per course row found in raw OCR text.

    Each dict has keys: course_code, credits, grade, class_average,
    std_dev, r_score.  Missing or out-of-range values are None.
    """
    results: list[dict] = []
    for line in raw_ocr_text.splitlines():
        line = line.strip()
        if not line:
            continue
        m = _RE_COURSE_CODE.search(line)
        if not m:
            mobile_row = _parse_mobile_fallback(line)
            if mobile_row:
                results.append(mobile_row)
            continue
        # Normalise separators to hyphens: "601 101 MQ" → "601-101-MQ"
        code = re.sub(r"[-\s]+", "-", m.group(1).upper()).strip("-")

        after = line[m.end():]
        nums = _extract_numbers(after)
        if not nums:
            continue

        fields = _assign_fields(nums)
        fields["course_code"] = code
        results.append(fields)

    return results
