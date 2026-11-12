"""Tests for parsers/omnivox_ocr_parser.py — no image I/O or Tesseract calls."""

import pytest
from parsers.omnivox_ocr_parser import parse_omnivox_table

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

STANDARD_OCR = """\
601-101-MQ  3  73.5  65.2  10.3  28.4
201-NYA-05  4  85.0  72.1  12.0  32.1
420-KDC-05  3  67.0  70.5   8.5  30.2
"""

FOUR_COL_OCR = """\
601-101-MQ  73.5  65.2  10.3  28.4
201-NYA-05  85.0  72.1  12.0  32.1
"""

NOISY_OCR = """\
601 101 MQ  3  73.5  65.2  10.3  28.4
Cours: 201-NYA-O5  4  85.0  72.1  12.0  32.1
"""

HEADERS_OCR = """\
Cours           Cr  Note  Moy  E-T  CRC
601-101-MQ      3   73.5  65.2 10.3 28.4
Semestre: A2024
201-NYA-05      4   85.0  72.1 12.0 32.1
"""

TRUNCATED_OCR = """\
601-101-MQ  3  73.5  65.2
201-NYA-05  4  85.0  72.1  12.0
"""

OUT_OF_BOUNDS_OCR = """\
601-101-MQ  3  73.5  65.2  10.3  55.5
201-NYA-05  3  73.5  65.2  10.3   2.1
"""

COMMA_DECIMAL_OCR = """\
601-101-MQ  3  73,5  65,2  10,3  28,4
"""

NO_STD_DEV_OCR = """\
601-101-MQ  3  73.5  65.2  28.4
201-NYA-05  4  85.0  72.1  32.1
"""

MOBILE_OCR = "91 76 34.815 2,66 A-202"


# ---------------------------------------------------------------------------
# Standard 5-column format
# ---------------------------------------------------------------------------

class TestStandard:
    def test_row_count(self):
        assert len(parse_omnivox_table(STANDARD_OCR)) == 3

    def test_first_course_code(self):
        assert parse_omnivox_table(STANDARD_OCR)[0]["course_code"] == "601-101-MQ"

    def test_first_credits(self):
        assert parse_omnivox_table(STANDARD_OCR)[0]["credits"] == 3.0

    def test_first_grade(self):
        assert parse_omnivox_table(STANDARD_OCR)[0]["grade"] == 73.5

    def test_first_class_average(self):
        assert parse_omnivox_table(STANDARD_OCR)[0]["class_average"] == 65.2

    def test_first_std_dev(self):
        assert parse_omnivox_table(STANDARD_OCR)[0]["std_dev"] == 10.3

    def test_first_r_score(self):
        assert parse_omnivox_table(STANDARD_OCR)[0]["r_score"] == 28.4

    def test_second_row(self):
        row = parse_omnivox_table(STANDARD_OCR)[1]
        assert row["course_code"] == "201-NYA-05"
        assert row["credits"] == 4.0
        assert row["r_score"] == 32.1

    def test_third_row(self):
        row = parse_omnivox_table(STANDARD_OCR)[2]
        assert row["course_code"] == "420-KDC-05"
        assert row["grade"] == 67.0
        assert row["r_score"] == 30.2


# ---------------------------------------------------------------------------
# 4-column format (no credits column)
# ---------------------------------------------------------------------------

class TestFourColumn:
    def setup_method(self):
        self.rows = parse_omnivox_table(FOUR_COL_OCR)

    def test_row_count(self):
        assert len(self.rows) == 2

    def test_no_credits(self):
        assert self.rows[0]["credits"] is None

    def test_grade_extracted(self):
        assert self.rows[0]["grade"] == 73.5

    def test_avg_extracted(self):
        assert self.rows[0]["class_average"] == 65.2

    def test_std_dev_extracted(self):
        assert self.rows[0]["std_dev"] == 10.3

    def test_r_score_extracted(self):
        assert self.rows[0]["r_score"] == 28.4


# ---------------------------------------------------------------------------
# OCR noise
# ---------------------------------------------------------------------------

class TestNoisyOCR:
    def setup_method(self):
        self.rows = parse_omnivox_table(NOISY_OCR)

    def test_row_count(self):
        assert len(self.rows) == 2

    def test_space_separated_code_normalised(self):
        assert self.rows[0]["course_code"] == "601-101-MQ"

    def test_prefix_text_ignored(self):
        assert self.rows[1]["course_code"] == "201-NYA-O5"

    def test_noisy_numbers_still_extracted(self):
        assert self.rows[0]["r_score"] == 28.4
        assert self.rows[1]["r_score"] == 32.1


# ---------------------------------------------------------------------------
# Header and non-data lines skipped
# ---------------------------------------------------------------------------

class TestHeadersSkipped:
    def setup_method(self):
        self.rows = parse_omnivox_table(HEADERS_OCR)

    def test_only_data_rows_returned(self):
        assert len(self.rows) == 2

    def test_first_data_row(self):
        assert self.rows[0]["course_code"] == "601-101-MQ"

    def test_second_data_row(self):
        assert self.rows[1]["course_code"] == "201-NYA-05"


# ---------------------------------------------------------------------------
# Truncated rows
# ---------------------------------------------------------------------------

class TestTruncated:
    def setup_method(self):
        self.rows = parse_omnivox_table(TRUNCATED_OCR)

    def test_row_count(self):
        assert len(self.rows) == 2

    def test_first_row_no_r_score(self):
        assert self.rows[0]["credits"] == 3.0
        assert self.rows[0]["grade"] == 73.5
        assert self.rows[0]["class_average"] == 65.2
        assert self.rows[0]["r_score"] is None

    def test_second_row_r_score_not_std_dev(self):
        assert self.rows[1]["credits"] == 4.0
        assert self.rows[1]["std_dev"] is None
        assert self.rows[1]["r_score"] == 12.0


# ---------------------------------------------------------------------------
# Out-of-bounds values
# ---------------------------------------------------------------------------

class TestOutOfBounds:
    def setup_method(self):
        self.rows = parse_omnivox_table(OUT_OF_BOUNDS_OCR)

    def test_r_score_above_50_not_assigned(self):
        assert self.rows[0]["r_score"] is None

    def test_r_score_below_5_not_assigned(self):
        assert self.rows[1]["r_score"] is None

    def test_grade_still_extracted_when_r_score_invalid(self):
        assert self.rows[0]["grade"] == 73.5


# ---------------------------------------------------------------------------
# Comma decimals
# ---------------------------------------------------------------------------

class TestCommaDecimals:
    def test_comma_numbers_parsed(self):
        rows = parse_omnivox_table(COMMA_DECIMAL_OCR)
        assert len(rows) == 1
        assert rows[0]["grade"] == 73.5
        assert rows[0]["r_score"] == 28.4


# ---------------------------------------------------------------------------
# Desktop layout with std_dev hidden
# ---------------------------------------------------------------------------

class TestNoStdDev:
    def setup_method(self):
        self.rows = parse_omnivox_table(NO_STD_DEV_OCR)

    def test_row_count(self):
        assert len(self.rows) == 2

    def test_first_r_score_extracted(self):
        assert self.rows[0]["r_score"] == 28.4

    def test_first_std_dev_is_none(self):
        assert self.rows[0]["std_dev"] is None

    def test_first_grade_and_avg_correct(self):
        assert self.rows[0]["grade"] == 73.5
        assert self.rows[0]["class_average"] == 65.2

    def test_first_credits_correct(self):
        assert self.rows[0]["credits"] == 3.0

    def test_second_r_score_extracted(self):
        assert self.rows[1]["r_score"] == 32.1

    def test_second_std_dev_is_none(self):
        assert self.rows[1]["std_dev"] is None


# ---------------------------------------------------------------------------
# Mobile / cropped fallback layout
# ---------------------------------------------------------------------------

class TestMobileFallback:
    def setup_method(self):
        self.rows = parse_omnivox_table(MOBILE_OCR)

    def test_row_count(self):
        assert len(self.rows) == 1

    def test_course_code_is_none(self):
        assert self.rows[0]["course_code"] is None

    def test_grade_extracted(self):
        assert self.rows[0]["grade"] == 91.0

    def test_average_extracted(self):
        assert self.rows[0]["class_average"] == 76.0

    def test_r_score_extracted(self):
        assert self.rows[0]["r_score"] == 34.815

    def test_credits_extracted(self):
        assert self.rows[0]["credits"] == 2.66

    def test_std_dev_is_none(self):
        assert self.rows[0]["std_dev"] is None

    def test_out_of_bounds_r_score_rejected(self):
        assert parse_omnivox_table("91 76 55.0 2,66") == []

    def test_non_numeric_line_not_matched(self):
        assert parse_omnivox_table("Login to Omnivox to view your grades") == []


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_empty_string_returns_empty_list(self):
        assert parse_omnivox_table("") == []

    def test_whitespace_only_returns_empty_list(self):
        assert parse_omnivox_table("   \n   \n   ") == []

    def test_no_course_code_lines_returns_empty_list(self):
        assert parse_omnivox_table("Total credits: 15\nGPA: 3.8\nSemestre A2024") == []

    def test_result_dicts_have_all_keys(self):
        expected = {"course_code", "credits", "grade", "class_average", "std_dev", "r_score"}
        for row in parse_omnivox_table(STANDARD_OCR):
            assert set(row.keys()) == expected

    def test_line_with_code_but_no_numbers_skipped(self):
        assert parse_omnivox_table("601-101-MQ  Écriture et littérature\n") == []

    def test_multiple_rows_order_preserved(self):
        codes = [r["course_code"] for r in parse_omnivox_table(STANDARD_OCR)]
        assert codes == ["601-101-MQ", "201-NYA-05", "420-KDC-05"]
