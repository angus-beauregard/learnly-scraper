"""Tests for workers/ocr/ocr_engine.py — pytesseract is mocked; Tesseract not required."""

import sys
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

# ---------------------------------------------------------------------------
# Inject a mock pytesseract before the module is imported so the real binary
# is never invoked.  This keeps the test suite runnable in CI without Tesseract.
# ---------------------------------------------------------------------------
_mock_tess = MagicMock()
_mock_tess.image_to_string.return_value = "601-101-MQ  3  73.5  65.2  10.3  28.4\n"
sys.modules.setdefault("pytesseract", _mock_tess)

from workers.ocr.ocr_engine import _TESS_CONFIG, _WHITELIST, extract_text_from_slide  # noqa: E402


# ---------------------------------------------------------------------------
# Config string
# ---------------------------------------------------------------------------

class TestTessConfig:
    def test_config_has_oem_3(self):
        assert "--oem 3" in _TESS_CONFIG

    def test_config_has_psm_6(self):
        assert "--psm 6" in _TESS_CONFIG

    def test_config_has_whitelist_flag(self):
        assert "tessedit_char_whitelist" in _TESS_CONFIG

    def test_whitelist_contains_digits(self):
        assert "0123456789" in _WHITELIST

    def test_whitelist_contains_uppercase(self):
        assert "ABCDEFGHIJKLMNOPQRSTUVWXYZ" in _WHITELIST

    def test_whitelist_contains_lowercase(self):
        assert "abcdefghijklmnopqrstuvwxyz" in _WHITELIST

    def test_whitelist_contains_common_punctuation(self):
        for ch in (".", ",", "-", "%", "/", ":"):
            assert ch in _WHITELIST, f"Missing: {ch!r}"


# ---------------------------------------------------------------------------
# extract_text_from_slide behaviour
# ---------------------------------------------------------------------------

class TestExtractText:
    def test_returns_string(self):
        img = np.zeros((100, 1200), dtype=np.uint8)
        result = extract_text_from_slide(img)
        assert isinstance(result, str)

    def test_calls_image_to_string(self):
        img = np.zeros((100, 1200), dtype=np.uint8)
        with patch("workers.ocr.ocr_engine.pytesseract") as mock_pt:
            mock_pt.image_to_string.return_value = "test output"
            result = extract_text_from_slide(img)
        mock_pt.image_to_string.assert_called_once()

    def test_passes_config_to_tesseract(self):
        img = np.zeros((100, 1200), dtype=np.uint8)
        with patch("workers.ocr.ocr_engine.pytesseract") as mock_pt:
            mock_pt.image_to_string.return_value = ""
            extract_text_from_slide(img)
            _, kwargs = mock_pt.image_to_string.call_args
            assert kwargs.get("config") == _TESS_CONFIG
