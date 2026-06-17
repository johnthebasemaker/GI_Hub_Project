"""
ai/cv/train.py — YOLOv8n training CLI for the returnable-tool detector
=======================================================================
Scans `data/cv_training/<class_name>/` for labelled images, trains a
YOLOv8n model, copies the resulting weights to a versioned directory
under `models/cv_returnable/v{N}/`, and registers the version in the
`cv_model_versions` table. NEVER auto-promotes — admin promotes via
the Tool Catalogue tab once they've reviewed the mAP score.

Dataset layout convention
-------------------------
    data/cv_training/
      ├── torque_wrench_12/
      │     ├── img_001.jpg       (image)
      │     ├── img_001.txt       (YOLO label: class_id cx cy w h, normalised)
      │     ├── img_002.jpg
      │     └── ...
      ├── multimeter_fluke/
      └── …

`data.yaml` and the train/val split files are generated INTO the
model output dir (e.g. `models/cv_returnable/v3/`). The `data/` dir
stays an immutable read-only image bank.

Usage
-----
    # Validate dataset without launching training.
    python ai/cv/train.py --dry-run

    # Train with default params (50 epochs, CPU, 80/20 split, seed=42).
    python ai/cv/train.py

    # Tweak hyperparameters / device.
    python ai/cv/train.py --epochs 100 --device 0
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import random
import shutil
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Repo paths
# ---------------------------------------------------------------------------
REPO_ROOT       = Path(__file__).resolve().parent.parent.parent
DEFAULT_DATASET = REPO_ROOT / "data" / "cv_training"
MODEL_ROOT      = REPO_ROOT / "models" / "cv_returnable"
IMG_EXTS        = (".jpg", ".jpeg", ".png", ".bmp")


# ---------------------------------------------------------------------------
# Dataset discovery + validation (no ML deps needed for these)
# ---------------------------------------------------------------------------
def discover_classes(dataset_dir: Path) -> list[str]:
    """Return sorted class names (one per subdirectory containing images)."""
    if not dataset_dir.exists():
        return []
    out = []
    for child in sorted(dataset_dir.iterdir()):
        if not child.is_dir():
            continue
        if any(p.suffix.lower() in IMG_EXTS for p in child.iterdir()):
            out.append(child.name)
    return out


def collect_pairs(dataset_dir: Path, classes: list[str]) -> dict[str, list[tuple[Path, Path]]]:
    """For each class, return list of (image_path, label_path) tuples.

    A pair is dropped (and reported) if the matching `.txt` label is missing
    — YOLO requires labels for every training image.
    """
    out: dict[str, list[tuple[Path, Path]]] = {}
    missing: list[Path] = []
    for cls in classes:
        cls_dir = dataset_dir / cls
        pairs = []
        for p in sorted(cls_dir.iterdir()):
            if p.suffix.lower() not in IMG_EXTS:
                continue
            label = p.with_suffix(".txt")
            if not label.exists():
                missing.append(p)
                continue
            pairs.append((p, label))
        out[cls] = pairs
    if missing:
        print(f"⚠️  {len(missing)} image(s) without matching .txt labels — they will be skipped:")
        for m in missing[:5]:
            print(f"    {m.relative_to(dataset_dir.parent)}")
        if len(missing) > 5:
            print(f"    … and {len(missing) - 5} more.")
    return out


def validate_dataset(pairs: dict[str, list]) -> list[str]:
    """Return a list of human-readable error messages. Empty = OK."""
    errors = []
    for cls, items in pairs.items():
        if len(items) == 0:
            errors.append(f"Class '{cls}' has zero labelled images.")
        elif len(items) < 5:
            errors.append(
                f"Class '{cls}' has only {len(items)} image(s) — YOLO needs "
                f"at least 5 to train, ideally 50+."
            )
    if not pairs:
        errors.append(
            f"No class directories found in dataset. Expected layout: "
            f"data/cv_training/<class_name>/*.jpg + matching .txt labels."
        )
    return errors


# ---------------------------------------------------------------------------
# Output dir auto-versioning
# ---------------------------------------------------------------------------
def next_version_dir(root: Path = MODEL_ROOT) -> tuple[str, Path]:
    """Scan root for existing v{N} subdirs; return (version_label, next_dir)."""
    root.mkdir(parents=True, exist_ok=True)
    existing = []
    for p in root.iterdir():
        if p.is_dir() and p.name.startswith("v") and p.name[1:].isdigit():
            existing.append(int(p.name[1:]))
    n = (max(existing) + 1) if existing else 1
    label = f"v{n}"
    return label, root / label


# ---------------------------------------------------------------------------
# Dataset split + data.yaml emission
# ---------------------------------------------------------------------------
def build_split_files(
    pairs: dict[str, list[tuple[Path, Path]]],
    out_dir: Path,
    val_split: float,
    seed: int,
) -> tuple[Path, Path]:
    """Write train.txt and val.txt (one image path per line, absolute)."""
    rng = random.Random(seed)
    train_paths, val_paths = [], []
    for cls, items in pairs.items():
        shuffled = items[:]
        rng.shuffle(shuffled)
        n_val = max(1, int(round(len(shuffled) * val_split))) if shuffled else 0
        for i, (img, _lab) in enumerate(shuffled):
            (val_paths if i < n_val else train_paths).append(str(img.resolve()))

    train_file = out_dir / "train.txt"
    val_file   = out_dir / "val.txt"
    train_file.write_text("\n".join(train_paths) + "\n", encoding="utf-8")
    val_file.write_text("\n".join(val_paths) + "\n", encoding="utf-8")
    return train_file, val_file


def write_data_yaml(
    out_dir: Path,
    classes: list[str],
    train_file: Path,
    val_file: Path,
) -> Path:
    """Emit ultralytics-compatible data.yaml."""
    yaml_path = out_dir / "data.yaml"
    lines = [
        f"# Auto-generated by ai/cv/train.py at {datetime.datetime.now().isoformat(timespec='seconds')}",
        f"train: {train_file.resolve()}",
        f"val:   {val_file.resolve()}",
        f"nc:    {len(classes)}",
        f"names: {classes!r}",
    ]
    yaml_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return yaml_path


# ---------------------------------------------------------------------------
# mAP extraction from ultralytics CSV output
# ---------------------------------------------------------------------------
def extract_map50(results_csv: Path) -> float | None:
    """Read the last row of results.csv and return mAP@0.5 if present."""
    if not results_csv.exists():
        return None
    rows = results_csv.read_text(encoding="utf-8").strip().splitlines()
    if len(rows) < 2:
        return None
    header = [c.strip() for c in rows[0].split(",")]
    last_row = [c.strip() for c in rows[-1].split(",")]
    # Ultralytics column name varies between versions — try common variants.
    for candidate in ("metrics/mAP50(B)", "metrics/mAP_0.5", "mAP50"):
        if candidate in header:
            try:
                return float(last_row[header.index(candidate)])
            except (ValueError, IndexError):
                pass
    return None


# ---------------------------------------------------------------------------
# Main flow
# ---------------------------------------------------------------------------
def run(args: argparse.Namespace) -> int:
    dataset_dir = Path(args.dataset).resolve()
    print(f"▶ Dataset:  {dataset_dir}")
    print(f"▶ Mode:     {'DRY-RUN' if args.dry_run else 'TRAIN'}")
    print()

    classes = discover_classes(dataset_dir)
    pairs   = collect_pairs(dataset_dir, classes)
    errors  = validate_dataset(pairs)

    print(f"▶ Discovered {len(classes)} class(es):")
    for cls in classes:
        n = len(pairs.get(cls, []))
        print(f"   · {cls:30s}  {n:>4d} labelled image(s)")
    if not classes:
        print("   (none)")
    print()

    if errors:
        print("✖ Dataset validation failed:")
        for e in errors:
            print(f"   - {e}")
        return 2

    if args.dry_run:
        print("✔ Dry-run only. Dataset looks good. Re-run without --dry-run to train.")
        return 0

    version_label, out_dir = next_version_dir(MODEL_ROOT)
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"▶ Output:   {out_dir}")
    print(f"▶ Version:  {version_label}")
    print()

    train_file, val_file = build_split_files(pairs, out_dir, args.val_split, args.seed)
    yaml_path = write_data_yaml(out_dir, classes, train_file, val_file)
    print(f"   wrote {yaml_path.name} · train.txt · val.txt")

    # Lazy-import ultralytics — keeps the CLI runnable for --dry-run on
    # machines without torch installed, and keeps test harnesses cheap.
    try:
        from ultralytics import YOLO
    except ImportError:
        print("✖ ultralytics not installed. Run: pip install 'ultralytics>=8.1'")
        return 3

    print(f"\n▶ Loading base model: {args.base}")
    model = YOLO(args.base)

    print(f"▶ Training: epochs={args.epochs} device={args.device} seed={args.seed}\n")
    results = model.train(
        data=str(yaml_path),
        epochs=args.epochs,
        device=args.device,
        seed=args.seed,
        project=str(out_dir / "runs"),
        name="train",
        exist_ok=True,
    )

    # Harvest weights + metrics.
    run_dir   = out_dir / "runs" / "train"
    weights   = run_dir / "weights" / "best.pt"
    csv_path  = run_dir / "results.csv"
    if not weights.exists():
        print(f"✖ best.pt not produced — check {run_dir}")
        return 4
    final_weights = out_dir / "best.pt"
    shutil.copy2(weights, final_weights)
    map50 = extract_map50(csv_path)

    training_log = {
        "version": version_label,
        "trained_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "epochs": args.epochs,
        "device": args.device,
        "seed": args.seed,
        "base_model": args.base,
        "classes": classes,
        "n_images_train": len(train_file.read_text().splitlines()),
        "n_images_val":   len(val_file.read_text().splitlines()),
        "mAP_0.5": map50,
        "weights_path": str(final_weights.resolve()),
    }
    (out_dir / "training_log.json").write_text(
        json.dumps(training_log, indent=2), encoding="utf-8",
    )

    # Register in the DB (NOT promoted to active — admin decides).
    sys.path.insert(0, str(REPO_ROOT))
    try:
        from database import register_cv_model_version
        row_id = register_cv_model_version(
            version_label,
            str(final_weights.resolve()),
            classes,
            map50,
        )
    except Exception as e:
        print(f"⚠️  Training completed but DB registration failed: {type(e).__name__}: {e}")
        print(f"   Weights at: {final_weights}")
        return 5

    print()
    print(f"✔ Trained {version_label}")
    print(f"   mAP@0.5  : {map50:.3f}" if map50 is not None else "   mAP@0.5  : (not parsed)")
    print(f"   weights  : {final_weights}")
    print(f"   DB row id: {row_id}  (is_active=0)")
    print()
    print(f"▶ NOT yet active. Promote in Admin Portal → 🛠️ Tool Catalogue → ✅ Promote {version_label}.")
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Train the returnable-tool detector (YOLOv8n).")
    p.add_argument("--dataset",   default=str(DEFAULT_DATASET),
                   help=f"Path to dataset root (default: {DEFAULT_DATASET}).")
    p.add_argument("--epochs",    type=int, default=50)
    p.add_argument("--device",    default="cpu",
                   help="'cpu' or a GPU index (e.g. '0').")
    p.add_argument("--base",      default="yolov8n.pt",
                   help="Base model checkpoint. Auto-downloaded on first run.")
    p.add_argument("--val-split", type=float, default=0.2, dest="val_split")
    p.add_argument("--seed",      type=int, default=42)
    p.add_argument("--dry-run",   action="store_true",
                   help="Validate dataset only — no training.")
    return p.parse_args(argv)


if __name__ == "__main__":
    sys.exit(run(parse_args()))
