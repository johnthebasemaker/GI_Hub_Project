# 01 · Image Preprocessing

> Accepts the uploaded form image, prepares it for OCR, and rejects images that cannot yield reliable results. Runs once per uploaded file.

**Depends on:** existing upload endpoint (GI_Hub already has one — reuse it).
**Feeds:** `02-header-extraction.md`.

---

## Purpose

Handwritten forms arrive as phone photos. They are:

- Rotated (portrait / landscape / upside-down)
- Skewed (photographed at an angle)
- Occasionally partially shadowed
- Sometimes cropped tight, sometimes with 40% desk background
- JPEG-compressed, often over-sharpened by the phone

The preprocessing stage does the minimum needed to hand a clean, correctly-oriented image to whatever OCR engine the project chooses. It never enhances *content* — it only fixes *presentation*.

---

## Contract

```
input:  {
  image: bytes,                     # raw upload
  filename: string,                 # original name, used only for logging
  mime_type: string,                # image/jpeg | image/png | image/webp
}

output: {
  processed_image: bytes,           # normalised, upright, cleaned
  original_dimensions: [w, h],
  final_dimensions: [w, h],
  rotation_applied: 0 | 90 | 180 | 270,
  rejected: bool,
  rejection_reason: string | null,
}
```

If `rejected` is true, no downstream stage runs. The Issue page surfaces the reason and asks the user to re-upload.

---

## Steps

Every step is optional individually but the order matters when multiple apply.

### 1. Format normalisation
- Accept `image/jpeg`, `image/png`, `image/webp`, `image/heic` (iPhone default).
- Convert everything to a common in-memory format (RGB PNG or PIL Image; project's choice).
- Reject anything else.

### 2. Orientation
- Read EXIF orientation tag if present and rotate accordingly.
- If EXIF is missing/unreliable, detect orientation by finding the header text `"MPC3P1-CNCEC PROJECT"` — it must appear near the top. If it appears at the bottom, rotate 180°. If it appears on a side, rotate 90° or 270° as needed.
- Store `rotation_applied` for audit.

### 3. Deskew (optional but recommended)
- Detect the dominant angle of the outer table border (a nearly-horizontal line).
- If the angle is within ±5°, apply the rotation to make it exactly horizontal.
- Do **not** attempt corrections > 5°; that suggests the photo is bad and should be re-taken.

### 4. Crop
- Crop tightly to the form's outer border (the black rectangle around the whole table).
- Leave a small margin (~2%) so edge text isn't clipped.
- If the border cannot be reliably detected, skip cropping. Do not fall back to arbitrary crops.

### 5. Downscale
- If the shortest side is > 2400 px, downscale so that the shortest side is 2400 px.
- This keeps LLM vision costs bounded without hurting handwriting readability.

### 6. Reject if unusable

Reject the image if **any** of the following are true:

| Condition | Rejection reason |
|---|---|
| Shortest side < 800 px after preprocessing | `image_too_small` |
| Header text `MPC3P1-CNCEC PROJECT` cannot be located | `header_not_found` |
| Detected skew > 15° | `image_too_skewed` |
| No table structure detected (no horizontal rules) | `not_a_form` |
| Uniform brightness variance < threshold (blank or blown-out) | `unreadable_exposure` |

These are non-blocking to the *user* — they just see "please re-upload"; no data is written.

---

## What This Stage Does NOT Do

- **No colour manipulation.** Do not convert to grayscale, do not enhance contrast, do not sharpen. Downstream OCR (whether LLM vision or Tesseract) does this better and with more context.
- **No text extraction.** That is file 02 onwards.
- **No content validation.** The presence of the header text is used only as an orientation cue; whether the *form* is valid is decided later.

---

## Pseudo-code

```python
from PIL import Image, ExifTags
import io

MAX_SHORT_SIDE   = 2400
MIN_SHORT_SIDE   = 800
MAX_SKEW_DEGREES = 15

def preprocess(image_bytes: bytes, mime: str) -> dict:
    if mime not in {"image/jpeg", "image/png", "image/webp", "image/heic"}:
        return _reject("unsupported_mime_type")

    img = Image.open(io.BytesIO(image_bytes))
    orig_dims = img.size

    # 1. Orientation via EXIF
    img, exif_rotation = _apply_exif_rotation(img)

    # 2. Refine orientation via header detection (optional)
    header_rotation = _detect_header_rotation(img)   # returns 0/90/180/270
    if header_rotation:
        img = img.rotate(-header_rotation, expand=True)
    total_rotation = (exif_rotation + header_rotation) % 360

    # 3. Deskew
    skew = _detect_skew_degrees(img)
    if skew is None:
        return _reject("not_a_form")
    if abs(skew) > MAX_SKEW_DEGREES:
        return _reject("image_too_skewed")
    if abs(skew) > 0.5:
        img = img.rotate(skew, expand=True, fillcolor="white")

    # 4. Crop to outer border
    bbox = _detect_outer_border(img)
    if bbox:
        img = img.crop(_expand(bbox, pct=0.02))

    # 5. Downscale
    w, h = img.size
    short = min(w, h)
    if short > MAX_SHORT_SIDE:
        scale = MAX_SHORT_SIDE / short
        img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)

    # 6. Final gates
    w, h = img.size
    if min(w, h) < MIN_SHORT_SIDE:
        return _reject("image_too_small")
    if not _header_present(img):
        return _reject("header_not_found")

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return {
        "processed_image": buf.getvalue(),
        "original_dimensions": orig_dims,
        "final_dimensions": img.size,
        "rotation_applied": total_rotation,
        "rejected": False,
        "rejection_reason": None,
    }

def _reject(reason: str) -> dict:
    return {
        "processed_image": None,
        "rejected": True,
        "rejection_reason": reason,
    }
```

The `_apply_exif_rotation`, `_detect_header_rotation`, `_detect_skew_degrees`, `_detect_outer_border`, and `_header_present` helpers are project-choice implementations (Pillow + OpenCV, or a vision LLM call, or a dedicated document-scanning library like `documentai` / `unpaper`). None of them are algorithmically hard; they are utilities.

---

## Interactions with Other Files

- **File 02** assumes it receives a right-side-up image with the header at the top.
- **File 08** may surface `rejected` as a batch-level flag if a user uploads multiple images and only some are rejected.
- **File 10** must handle mixed batches — some rejected, some accepted — without failing the whole submission.

---

## Claude Code Metadata

```yaml
module: image-preprocessing
version: 1.0
depends_on: [upload-endpoint]
feeds: [header-extraction]

constants:
  MAX_SHORT_SIDE: 2400
  MIN_SHORT_SIDE: 800
  MAX_SKEW_DEGREES: 15
  CROP_MARGIN_PCT: 0.02
  ACCEPTED_MIME_TYPES:
    - image/jpeg
    - image/png
    - image/webp
    - image/heic

steps_in_order:
  - format_normalisation
  - exif_orientation
  - header_based_orientation
  - deskew
  - crop_to_border
  - downscale
  - reject_gates

rejection_reasons:
  - unsupported_mime_type
  - image_too_small
  - image_too_skewed
  - not_a_form
  - header_not_found
  - unreadable_exposure

prohibited_operations:
  - grayscale_conversion
  - contrast_enhancement
  - sharpening
  - denoising_filters
  - jpeg_recompression_above_source_quality
```
