"""Select and download the dataset images from COCO val2017.

    uv run --group dataset python dataset/generators/fetch_coco.py [--seed 42]

Why COCO: the image worker runs YOLOv8n trained on the COCO classes and turns
``knife``/``scissors`` detections into ``sensitive_image`` findings. COCO's own
human annotations give us real bounding boxes for exactly those classes, so the
ground truth is independent of the model under test.

Steps (all output under ``dataset/build/coco/``, which git ignores):

1. Download ``annotations_trainval2017.zip`` (about 241 MB) once and read
   ``instances_val2017.json``.
2. Keep images whose Flickr license allows redistribution with attribution
   (COCO license ids 4 CC BY 2.0, 5 CC BY-SA 2.0, 6 CC BY-ND 2.0, 7 no known
   copyright restrictions, 8 United States Government Work). NonCommercial
   licenses (1-3) are excluded.
3. Seeded sample: ``POSITIVES`` images with at least one non-crowd knife or
   scissors box and ``NEGATIVES`` images with none.
4. Download each selected image from images.cocodataset.org.

Writes ``dataset/generators/data/coco_selection.json`` (committed): image id,
file name, license, Flickr URL and the normalized knife/scissors boxes. This
file is the provenance record and the image ground truth.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import urllib.request
import zipfile
from pathlib import Path

DATASET_ROOT = Path(__file__).resolve().parents[1]
CACHE = DATASET_ROOT / "build" / "coco"
SELECTION = Path(__file__).resolve().parent / "data" / "coco_selection.json"
ANNOTATIONS_URL = (
    "http://images.cocodataset.org/annotations/annotations_trainval2017.zip"
)
IMAGE_URL = "http://images.cocodataset.org/val2017/{file_name}"
ALLOWED_LICENSES = {4, 5, 6, 7, 8}
SENSITIVE_CLASSES = {"knife", "scissors"}
POSITIVES = 73  # every permissively licensed val2017 image with a knife/scissors box
NEGATIVES = 237


def _download(url: str, dest: Path) -> None:
    if dest.is_file() and dest.stat().st_size > 0:
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    with urllib.request.urlopen(url, timeout=120) as resp, tmp.open("wb") as out:
        while block := resp.read(1024 * 1024):
            out.write(block)
    tmp.replace(dest)


def load_annotations() -> dict:
    archive = CACHE / "annotations_trainval2017.zip"
    extracted = CACHE / "instances_val2017.json"
    if not extracted.is_file():
        print(f"downloading {ANNOTATIONS_URL} ...", file=sys.stderr)
        _download(ANNOTATIONS_URL, archive)
        with zipfile.ZipFile(archive) as zf:
            extracted.write_bytes(zf.read("annotations/instances_val2017.json"))
    return json.loads(extracted.read_text(encoding="utf-8"))


def select(coco: dict, seed: int) -> dict:
    categories = {c["id"]: c["name"] for c in coco["categories"]}
    licenses = {lic["id"]: lic for lic in coco["licenses"]}
    images = {
        img["id"]: img
        for img in coco["images"]
        if img["license"] in ALLOWED_LICENSES
    }
    boxes: dict[int, list[dict]] = {}
    touched: set[int] = set()  # any knife/scissors annotation, crowd included
    for ann in coco["annotations"]:
        name = categories[ann["category_id"]]
        if name not in SENSITIVE_CLASSES or ann["image_id"] not in images:
            continue
        touched.add(ann["image_id"])
        if ann.get("iscrowd"):
            continue
        img = images[ann["image_id"]]
        x, y, w, h = ann["bbox"]
        boxes.setdefault(ann["image_id"], []).append(
            {
                "label": name,
                "area_px": round(ann["area"], 1),
                "bbox": {
                    "x": round(x / img["width"], 6),
                    "y": round(y / img["height"], 6),
                    "w": round(min(w / img["width"], 1 - x / img["width"]), 6),
                    "h": round(min(h / img["height"], 1 - y / img["height"]), 6),
                },
            }
        )
    rng = random.Random(seed)
    positive_ids = sorted(boxes)
    negative_ids = sorted(set(images) - touched)
    if len(positive_ids) < POSITIVES:
        raise SystemExit(f"only {len(positive_ids)} positive images available")
    chosen_pos = sorted(rng.sample(positive_ids, POSITIVES))
    chosen_neg = sorted(rng.sample(negative_ids, NEGATIVES))
    selected = []
    for image_id in chosen_pos + chosen_neg:
        img = images[image_id]
        lic = licenses[img["license"]]
        selected.append(
            {
                "image_id": image_id,
                "file_name": img["file_name"],
                "width": img["width"],
                "height": img["height"],
                "flickr_url": img.get("flickr_url", ""),
                "coco_url": img.get("coco_url", ""),
                "license_id": lic["id"],
                "license": lic["name"],
                "license_url": lic["url"],
                "sensitive_boxes": boxes.get(image_id, []),
            }
        )
    return {
        "source": "COCO 2017 val (https://cocodataset.org), annotations CC BY 4.0",
        "seed": seed,
        "allowed_license_ids": sorted(ALLOWED_LICENSES),
        "positives": POSITIVES,
        "negatives": NEGATIVES,
        "available_positive_images": len(positive_ids),
        "images": selected,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--skip-images", action="store_true", help="only write the selection"
    )
    args = parser.parse_args(argv)

    selection = select(load_annotations(), args.seed)
    SELECTION.parent.mkdir(parents=True, exist_ok=True)
    SELECTION.write_text(json.dumps(selection, indent=1) + "\n", encoding="utf-8", newline="\n")
    print(f"wrote {SELECTION} ({len(selection['images'])} images)", file=sys.stderr)
    if args.skip_images:
        return 0
    images_dir = CACHE / "val2017"
    for i, img in enumerate(selection["images"], 1):
        _download(IMAGE_URL.format(file_name=img["file_name"]), images_dir / img["file_name"])
        if i % 50 == 0:
            print(f"  {i}/{len(selection['images'])} images", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
