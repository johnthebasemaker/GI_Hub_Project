# CV Training Guide

This guide explains how to capture training images, label them, and run the
training CLI to produce a versioned tool-detection model for the
Returnable Items flow.

## 1. Why we trained our own model

The pretrained YOLOv8n COCO model can spot generic objects (people,
laptops, bottles) but knows nothing about **our** torque wrenches, specific
multimeter models, or branded calibration tools. We fine-tune YOLOv8n on
our own labelled images so the detector reports the exact tool class our
inventory uses (e.g. `torque_wrench_12`), not "tool" or "wrench".

## 2. Capturing training images

**Target:** 50 images per tool class minimum. Models trained on fewer than
30/class typically produce poor mAP and false positives in the storeroom.

Capture rules:
- **Vary the angle** — top-down, 45°, side-on. Don't shoot 50 frames of
  the same pose.
- **Vary the background** — workshop table, on the floor, on shelves,
  partially occluded. The detector needs to learn "this is the tool"
  even when half of it is behind another item.
- **Realistic lighting** — overhead fluorescent IS what the storeroom
  looks like. Don't capture in a photo studio.
- **Include the bad cases** — partially-open boxes, tools wrapped in
  plastic, tools next to lookalike items. These edge cases drive
  real-world accuracy.
- **No backgrounds the camera will never see** — don't include outdoor
  shots if the storeroom is indoor.

## 3. Labeling

Pick one of two paths. Both produce the YOLO `.txt` format our CLI expects.

### Option A — LabelImg (free, local)

```
pip install labelImg
labelImg
```

In LabelImg: **View → Auto Save Mode**, **PascalVOC → YOLO** format, set
the output directory to your `data/cv_training/<class_name>/` folder.
Draw a tight bounding box around the tool in each image. The tool will
emit a matching `.txt` file beside every image.

### Option B — Roboflow (hosted)

1. Create a Roboflow project, upload images per class.
2. Annotate via their web UI.
3. **Export → YOLOv8 → Download zip.**
4. Unzip into `data/cv_training/` — the export structure already matches
   our convention.

## 4. Directory layout

```
data/cv_training/
  ├── torque_wrench_12/
  │     ├── img_001.jpg
  │     ├── img_001.txt    # YOLO label: <class_id> <cx> <cy> <w> <h> (normalised)
  │     ├── img_002.jpg
  │     ├── img_002.txt
  │     └── ...
  ├── multimeter_fluke_117/
  ├── crimper_klein/
  └── ...
```

`<class_id>` inside each `.txt` is **always 0** for single-class folders —
the training CLI maps directory names to class indices at training time.

## 5. Running training

Validate the dataset first (no GPU, no waiting):

```bash
.venv/bin/python ai/cv/train.py --dry-run
```

If the dry-run passes, train for real:

```bash
# CPU (slow but no GPU needed — fine on M-series Macs, ~20 min for 50 epochs on 200 images)
.venv/bin/python ai/cv/train.py --epochs 50

# GPU (CUDA index)
.venv/bin/python ai/cv/train.py --epochs 50 --device 0
```

The CLI:
1. Discovers classes by directory name.
2. Auto-versions: scans `models/cv_returnable/` and writes the next `v{N}`.
3. Generates `data.yaml` + `train.txt` + `val.txt` (80/20 split, seed=42 for reproducibility).
4. Calls `ultralytics.YOLO('yolov8n.pt').train(...)`. The base weights are auto-downloaded on first run (~6 MB).
5. Copies `best.pt` to `models/cv_returnable/v{N}/best.pt`.
6. Extracts `mAP@0.5` and writes `training_log.json`.
7. Registers the version in the `cv_model_versions` DB table with `is_active=0`.

**Important:** the new version is NOT auto-promoted. It sits in the DB
waiting for admin review.

## 6. Interpreting mAP@0.5

| mAP@0.5 | Verdict |
|---|---|
| **≥ 0.85** | Excellent. Ready for the storeroom. |
| **0.70 – 0.85** | Decent but add 20-30 more images per class and retrain. |
| **< 0.70** | Don't promote. Most likely: too few images, mislabelled boxes, or backgrounds too uniform. |

mAP@0.5 = mean Average Precision at IoU threshold 0.5. It's the standard
YOLO benchmark — high = detector finds the right tool in the right
location most of the time.

## 7. Promoting in the UI

Once `mAP@0.5` looks good:

1. Open **Admin Portal → 🛠️ Tool Catalogue → 📦 Model Versions**.
2. Find the new `v{N}` row.
3. Pick it in the "Version to promote" selectbox.
4. Click **✅ Promote**.

What happens:
- `promote_cv_model_version()` atomically demotes the old active row and
  flips the new one to `is_active=1`.
- `invalidate_model_cache()` clears the in-memory YOLO model so the very
  next `detect_tool()` call (typically in the Returnable Items Smart Scan
  flow) re-loads the new weights. **No server restart required.**

## 8. Adding a new class to an existing model

You **must retrain** the model from scratch. YOLOv8 doesn't support
incremental fine-tuning out of the box — adding `respirator_3m` to a
model that knows only torque wrenches would degrade the wrench class
without retraining.

Workflow:

1. Add `data/cv_training/respirator_3m/` with 50+ labelled images.
2. Re-run `python ai/cv/train.py --epochs 50`. Output goes to `v{N+1}`.
3. In the UI, **re-register the tool classes** for the new version (set
   their `model_version_id` to the new id).
4. Promote `v{N+1}`.

## 9. Confidence thresholds — when to deviate from 0.75

Defaults set in `config.py` and `ai/cv/inference.py`:
- `DEFAULT_MIN_CONFIDENCE = 0.75` — applied to any tool class that doesn't
  override it.

Per-class overrides in **Admin Portal → 🛠️ Tool Catalogue → 🏷️ Classes**:
- **Raise to 0.85+** for safety-critical tools (respirators, calibration
  references). False positives here are costly.
- **Lower to 0.65** for high-volume low-value items where missing a
  detection is more annoying than a wrong one.

The threshold is enforced inside `detect_tool` — detections below it are
silently dropped before the UI ever sees them.

## 10. Where files live

| Artifact | Location | Backed up? |
|---|---|---|
| Training images | `data/cv_training/` | Separate backup (large; rsync to NAS or B2) |
| Model weights | `models/cv_returnable/v{N}/best.pt` | Yes — `host_setup/scripts/backup_db.sh` should include `models/` |
| Training log | `models/cv_returnable/v{N}/training_log.json` | Yes, alongside weights |
| DB row | `cv_model_versions` in `gi_database.db` | Yes — covered by the existing DB backup |

The on-disk artifacts and the DB row are the **two sources of truth**.
The Storage Inspector sub-tab in the UI flags any mismatch.
