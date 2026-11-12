"""
Tesseract OCR wrapper for preprocessed transcript images.

Uses the C-engine Tesseract binary via pytesseract.  No PyTorch, no EasyOCR —
both exceed the Raspberry Pi 3 RSS budget (< 150 MB).

Config:
  --oem 3   LSTM + legacy engine (best accuracy on printed text)
  --psm 6   Assume a uniform block of text (single-column transcript table)
  Character whitelist: printable ASCII chars that appear in Omnivox tables
"""

import numpy as np
import pytesseract

_WHITELIST = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz.,-% /:"
_TESS_CONFIG = f'--oem 3 --psm 6 -c "tessedit_char_whitelist={_WHITELIST}"'


def extract_text_from_slide(processed_img: np.ndarray) -> str:
    return pytesseract.image_to_string(processed_img, config=_TESS_CONFIG)
