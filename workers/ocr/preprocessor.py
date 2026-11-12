"""
Image preprocessing pipeline for Omnivox transcript screenshots.

Pillow + NumPy only — no OpenCV.  There is no prebuilt opencv-python-headless
wheel for linux/arm/v7 (Raspberry Pi 3), and Pillow achieves identical results
for the thresholding step needed here.

Pipeline:
  1. Decode raw bytes → Pillow RGB image
  2. Upscale to 1200 px wide (BICUBIC) if smaller — Tesseract accuracy drops
     sharply on sub-800px text
  3. Grayscale
  4. Gaussian blur (radius 1) to reduce JPEG compression artefacts
  5. Otsu-style global threshold → binary image
  6. Return as np.ndarray (uint8, shape H×W) for pytesseract
"""

import io

import numpy as np
from PIL import Image, ImageFilter


def preprocess_transcript_image(raw_bytes: bytes) -> np.ndarray:
    img = Image.open(io.BytesIO(raw_bytes)).convert("RGB")

    target_width = 1200
    if img.width < target_width:
        scale = target_width / img.width
        new_size = (target_width, int(img.height * scale))
        img = img.resize(new_size, Image.Resampling.BICUBIC)

    gray = img.convert("L")
    blurred = gray.filter(ImageFilter.GaussianBlur(radius=1))

    arr = np.array(blurred, dtype=np.uint8)
    threshold = int(arr.mean())
    binary = np.where(arr > threshold, 255, 0).astype(np.uint8)

    return binary
