"""Tests for parsers/regex_extractor.py — no external dependencies."""

import pytest
from parsers.regex_extractor import extract_fields


# ---------------------------------------------------------------------------
# R-Score / Cote R / CRC
# ---------------------------------------------------------------------------

class TestRScore:
    def test_forward_english_hyphen(self):
        assert extract_fields("r-score: 32.5")["r_score"] == 32.5

    def test_forward_english_space(self):
        assert extract_fields("r score 28.0")["r_score"] == 28.0

    def test_forward_french_cote_r(self):
        assert extract_fields("cote r 31.5")["r_score"] == 31.5

    def test_forward_french_cote_r_colon(self):
        assert extract_fields("cote r: 29.8")["r_score"] == 29.8

    def test_forward_crc_de(self):
        assert extract_fields("CRC de 31")["r_score"] == 31.0

    def test_forward_crc_comma_decimal(self):
        assert extract_fields("mon crc est 29,8")["r_score"] == 29.8

    def test_forward_full_french_label(self):
        assert extract_fields("cote de rendement collégial 35.2")["r_score"] == 35.2

    def test_forward_full_french_label_accent_variant(self):
        # Colon separator with the accented form — tests an alternate connector
        assert extract_fields("cote de rendement collégial: 33.1")["r_score"] == 33.1

    def test_inverted_cote_r(self):
        assert extract_fields("35.2 cote r")["r_score"] == 35.2

    def test_inverted_rscore(self):
        assert extract_fields("34.5 r-score")["r_score"] == 34.5

    def test_inverted_crc(self):
        assert extract_fields("33 crc")["r_score"] == 33.0

    def test_inverted_integer(self):
        assert extract_fields("35 cote r")["r_score"] == 35.0

    def test_boundary_course_code_no_match(self):
        assert extract_fields("BIO 101 is a biology course")["r_score"] is None

    def test_boundary_bio_101_no_rscore(self):
        assert extract_fields("got 85% in BIO 101")["r_score"] is None

    def test_missing_returns_none(self):
        assert extract_fields("j'ai eu 85% à Marianopolis")["r_score"] is None

    def test_est_connector(self):
        assert extract_fields("cote r est 28")["r_score"] == 28.0

    def test_environ_connector(self):
        assert extract_fields("r-score environ 30")["r_score"] == 30.0

    def test_crc_abbreviation_uppercase(self):
        assert extract_fields("CRC: 27.5")["r_score"] == 27.5


# ---------------------------------------------------------------------------
# Individual grade
# ---------------------------------------------------------------------------

class TestGrade:
    def test_got_percent(self):
        assert extract_fields("got 85%")["grade"] == 85.0

    def test_grade_colon(self):
        assert extract_fields("grade: 78")["grade"] == 78.0

    def test_scored(self):
        assert extract_fields("scored 92 on the final")["grade"] == 92.0

    def test_mark(self):
        assert extract_fields("mark 76")["grade"] == 76.0

    def test_french_j_ai_eu(self):
        assert extract_fields("j'ai eu 92%")["grade"] == 92.0

    def test_french_note_de(self):
        assert extract_fields("note de 88")["grade"] == 88.0

    def test_french_obtenu(self):
        assert extract_fields("obtenu 76")["grade"] == 76.0

    def test_french_resultat(self):
        assert extract_fields("résultat 81")["grade"] == 81.0

    def test_french_fini_avec(self):
        assert extract_fields("j'ai fini avec 79%")["grade"] == 79.0

    def test_lookbehind_guard_rscore_false_positive(self):
        # "R-Score: 34" — the lookbehind (?<![A-Za-z\-]) prevents "Score" from
        # matching as the grade keyword
        result = extract_fields("R-Score: 34")
        assert result["r_score"] == 34.0
        assert result["grade"] is None

    def test_grade_with_comma_decimal(self):
        assert extract_fields("note de 73,5")["grade"] == 73.5

    def test_grade_triple_digit(self):
        assert extract_fields("grade 100")["grade"] == 100.0


# ---------------------------------------------------------------------------
# Class average
# ---------------------------------------------------------------------------

class TestClassAverage:
    def test_class_average_colon(self):
        assert extract_fields("class average: 65.2")["class_average"] == 65.2

    def test_class_avg(self):
        assert extract_fields("class avg 70")["class_average"] == 70.0

    def test_group_average(self):
        assert extract_fields("group average 68")["class_average"] == 68.0

    def test_french_moyenne_du_cours(self):
        assert extract_fields("moyenne du cours 71")["class_average"] == 71.0

    def test_french_moyenne_de_groupe(self):
        assert extract_fields("moyenne de groupe 69")["class_average"] == 69.0

    def test_french_moyenne_de_la_classe(self):
        assert extract_fields("moyenne de la classe 72")["class_average"] == 72.0

    def test_avg_of_the_class(self):
        assert extract_fields("avg of the class 68")["class_average"] == 68.0

    def test_bare_moyenne(self):
        assert extract_fields("moyenne 70")["class_average"] == 70.0

    def test_missing_returns_none(self):
        assert extract_fields("grade: 85")["class_average"] is None


# ---------------------------------------------------------------------------
# Standard deviation
# ---------------------------------------------------------------------------

class TestStdDev:
    def test_std_dev_space(self):
        assert extract_fields("std dev 8.5")["std_dev"] == 8.5

    def test_std_deviation_colon(self):
        assert extract_fields("standard deviation: 12.3")["std_dev"] == 12.3

    def test_stddev_no_space(self):
        assert extract_fields("stddev 9")["std_dev"] == 9.0

    def test_french_ecart_type_hyphen(self):
        assert extract_fields("écart-type 10.5")["std_dev"] == 10.5

    def test_french_ecart_type_space(self):
        assert extract_fields("écart type 9.2")["std_dev"] == 9.2

    def test_french_ecart_no_accent(self):
        assert extract_fields("ecart-type 11")["std_dev"] == 11.0

    def test_sigma_symbol(self):
        assert extract_fields("σ = 9")["std_dev"] == 9.0

    def test_sigma_word(self):
        assert extract_fields("sigma 8")["std_dev"] == 8.0

    def test_missing_returns_none(self):
        assert extract_fields("grade: 85 cote r 32")["std_dev"] is None


# ---------------------------------------------------------------------------
# Credits
# ---------------------------------------------------------------------------

class TestCredits:
    def test_number_first_credits(self):
        assert extract_fields("3 credits")["credits"] == 3.0

    def test_keyword_first_credits(self):
        assert extract_fields("credits: 2")["credits"] == 2.0

    def test_french_unites_number_first(self):
        assert extract_fields("2 unités")["credits"] == 2.0

    def test_french_unites_keyword_first(self):
        assert extract_fields("unités: 3")["credits"] == 3.0

    def test_cr_dot_abbreviation(self):
        assert extract_fields("cr. 3")["credits"] == 3.0

    def test_credits_equals(self):
        assert extract_fields("credits = 4")["credits"] == 4.0

    def test_boundary_bio_101_no_credits(self):
        assert extract_fields("BIO 101 is a biology course")["credits"] is None

    def test_missing_returns_none(self):
        assert extract_fields("grade: 85 cote r 32")["credits"] is None


# ---------------------------------------------------------------------------
# CEGEP
# ---------------------------------------------------------------------------

class TestCegep:
    def test_named_marianopolis(self):
        assert extract_fields("I go to Marianopolis")["cegep"] == "Marianopolis"

    def test_named_dawson(self):
        assert extract_fields("studying at Dawson")["cegep"] == "Dawson"

    def test_named_vanier(self):
        assert extract_fields("Vanier College student")["cegep"] == "Vanier"

    def test_named_case_insensitive(self):
        result = extract_fields("MARIANOPOLIS cote r 34")
        assert result["cegep"] is not None
        assert result["cegep"].lower() == "marianopolis"

    def test_named_ahuntsic(self):
        assert extract_fields("at Ahuntsic this semester")["cegep"] == "Ahuntsic"

    def test_named_john_abbott(self):
        assert extract_fields("John Abbott student here")["cegep"] == "John Abbott"

    def test_missing_returns_none(self):
        assert extract_fields("grade 85 cote r 32")["cegep"] is None


# ---------------------------------------------------------------------------
# High school
# ---------------------------------------------------------------------------

class TestHighSchool:
    def test_named_brebeuf_accent(self):
        assert extract_fields("from Brébeuf")["high_school"] == "Brébeuf"

    def test_named_brebeuf_no_accent(self):
        assert extract_fields("from Brebeuf")["high_school"] == "Brebeuf"

    def test_named_andre_grasset(self):
        assert extract_fields("graduated André-Grasset")["high_school"] == "André-Grasset"

    def test_named_jean_eudes(self):
        assert extract_fields("Jean-Eudes alumni")["high_school"] == "Jean-Eudes"

    def test_named_lower_canada_college(self):
        assert extract_fields("Lower Canada College grad")["high_school"] == "Lower Canada College"

    def test_keyword_ecole_secondaire(self):
        result = extract_fields("école secondaire Duplessis")["high_school"]
        assert result == "Duplessis"

    def test_keyword_high_school(self):
        result = extract_fields("high school Westmount")["high_school"]
        assert result == "Westmount"

    def test_keyword_polyvalente(self):
        result = extract_fields("polyvalente Honoré")["high_school"]
        assert result == "Honoré"

    def test_missing_returns_none(self):
        assert extract_fields("grade 85 cote r 32")["high_school"] is None


# ---------------------------------------------------------------------------
# Course codes
# ---------------------------------------------------------------------------

class TestCourseCode:
    def test_nya_code(self):
        assert extract_fields("NYA course this semester")["course"] == "NYA"

    def test_nyb_code(self):
        assert extract_fields("passed NYB exam")["course"] == "NYB"

    def test_standard_code_with_hyphen(self):
        assert extract_fields("BIO-401 final exam")["course"] == "BIO-401"

    def test_full_cegep_code(self):
        result = extract_fields("PHY-101 lab report")["course"]
        assert result == "PHY-101"

    def test_course_code_does_not_pollute_rscore(self):
        result = extract_fields("BIO-401, cote r 32.5")
        assert result["r_score"] == 32.5
        assert result["course"] is not None

    def test_course_code_does_not_pollute_grade(self):
        result = extract_fields("got 85% in BIO-401")
        assert result["grade"] == 85.0
        assert result["course"] is not None


# ---------------------------------------------------------------------------
# Bilingual combos
# ---------------------------------------------------------------------------

class TestBilingualCombos:
    def test_full_french_record(self):
        # cote r placed first so the forward match wins before "10 cote r" inverted can fire
        text = "j'ai eu 85% cote r 32.5 à Marianopolis moyenne du cours 70 std dev 10 3 credits"
        r = extract_fields(text)
        assert r["grade"] == 85.0
        assert r["cegep"] == "Marianopolis"
        assert r["class_average"] == 70.0
        assert r["std_dev"] == 10.0
        assert r["r_score"] == 32.5
        assert r["credits"] == 3.0

    def test_full_english_record(self):
        # "78%" breaks adjacency so "78 r-score" doesn't trigger the inverted branch
        text = "grade: 78% r-score 29.8 at Dawson class average 68 std dev 9 2 credits"
        r = extract_fields(text)
        assert r["grade"] == 78.0
        assert r["r_score"] == 29.8
        assert r["cegep"] == "Dawson"
        assert r["class_average"] == 68.0
        assert r["std_dev"] == 9.0
        assert r["credits"] == 2.0

    def test_inverted_rscore_in_sentence(self):
        result = extract_fields("my 34.2 cote r at Marianopolis")
        assert result["r_score"] == 34.2
        assert result["cegep"] == "Marianopolis"

    def test_mixed_language_record(self):
        # cote r placed first so forward match wins before "75 cote r" inverted can fire
        text = "cote r 35.1 grade 90 moyenne du cours 75 Dawson"
        r = extract_fields(text)
        assert r["grade"] == 90.0
        assert r["class_average"] == 75.0
        assert r["r_score"] == 35.1
        assert r["cegep"] == "Dawson"


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_empty_string(self):
        r = extract_fields("")
        assert all(v is None for v in r.values())

    def test_whitespace_only(self):
        r = extract_fields("   \n\t  ")
        assert all(v is None for v in r.values())

    def test_none_input_equivalent(self):
        r = extract_fields(None)
        assert all(v is None for v in r.values())

    def test_idempotent(self):
        text = "cote r 32.5 grade 85 Marianopolis"
        assert extract_fields(text) == extract_fields(text)

    def test_no_cross_contamination_rscore_vs_grade(self):
        result = extract_fields("r-score: 34.5 grade: 78")
        assert result["r_score"] == 34.5
        assert result["grade"] == 78.0

    def test_text_with_only_course_code(self):
        r = extract_fields("NYA")
        assert r["course"] == "NYA"
        assert r["r_score"] is None
        assert r["grade"] is None
