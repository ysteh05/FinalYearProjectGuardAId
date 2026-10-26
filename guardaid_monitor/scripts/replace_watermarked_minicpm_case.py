"""Replace the watermarked ACC-VA-01 copy while preserving an audit archive."""
from __future__ import annotations

import csv
import hashlib
import json
import shutil
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CASE_DIR = ROOT / "VLM Fine-tune" / "Acceptance Set" / "Visually Ambiguous Objects" / "Case 01"
ARCHIVE = ROOT / "VLM Fine-tune" / "Excluded Before Freeze" / "ACC-VA-01-watermarked"
EVIDENCE_DIR = ROOT / "guardaid_monitor" / "runtime_data" / "media" / "evidence" / "alert_3871964601a84fea8ea45d9719d3346c"
SOURCE_FILES = [
    "8f0abc4b659f440fb039a0f61ad3f441.jpg",
    "343e0167684f41a8a2f3e70e6701182f.jpg",
    "af2bf88bb6e748bb8dcf54183d590f11.jpg",
    "2287aa5d8b6e4aa3b0b3038844053eef.jpg",
]
ALERT_ID = "alert_3871964601a84fea8ea45d9719d3346c"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def within(path: Path, parent: Path) -> bool:
    return path.resolve().is_relative_to(parent.resolve())


def main() -> None:
    if not within(CASE_DIR, ROOT / "VLM Fine-tune") or not within(ARCHIVE, ROOT / "VLM Fine-tune"):
        raise RuntimeError("Resolved curation paths escaped VLM Fine-tune")
    if not within(EVIDENCE_DIR, ROOT / "guardaid_monitor" / "runtime_data" / "media" / "evidence"):
        raise RuntimeError("Resolved source path escaped the evidence folder")
    metadata_path = CASE_DIR / "case.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    if metadata.get("case_id") != "ACC-VA-01":
        raise RuntimeError("The selected case is not ACC-VA-01")

    source_paths = [EVIDENCE_DIR / name for name in SOURCE_FILES]
    if any(not path.is_file() for path in source_paths):
        raise FileNotFoundError("One or more selected unwatermarked alert evidence files are missing")
    source_hashes = [sha256(path) for path in source_paths]

    current_media = CASE_DIR / "media"
    current_hashes = [sha256(path) for path in sorted(current_media.glob("*.jpg"))]
    if metadata.get("alert_id") == ALERT_ID:
        if metadata.get("copied_media_sha256") == source_hashes:
            print("[already-replaced] ACC-VA-01 matches the selected saved evidence.", flush=True)
            return
        raise RuntimeError("ACC-VA-01 points to the replacement alert but has mismatched media hashes")
    if ARCHIVE.exists():
        raise FileExistsError(f"Refusing to overwrite the preserved pre-freeze copy: {ARCHIVE}")

    ARCHIVE.mkdir(parents=True, exist_ok=False)
    shutil.copy2(metadata_path, ARCHIVE / "case.before.json")
    old_paths = sorted(current_media.glob("*.jpg"))
    for old_path in old_paths:
        shutil.move(str(old_path), str(ARCHIVE / old_path.name))
    if len(old_paths) != len(current_hashes):
        raise RuntimeError("Active case media changed during replacement")

    new_names = []
    for index, source in enumerate(source_paths, start=1):
        name = f"{index:02d}.jpg"
        target = current_media / name
        shutil.copy2(source, target)
        if sha256(target) != source_hashes[index - 1]:
            raise RuntimeError(f"Copied evidence hash mismatch: {target}")
        new_names.append(name)

    rel_media = [
        str(path.relative_to(ROOT)).replace("/", "\\") for path in source_paths
    ]
    copied_media = [
        str(Path("Acceptance Set") / "Visually Ambiguous Objects" / "Case 01" / "media" / name).replace("/", "\\")
        for name in new_names
    ]
    metadata.update({
        "source_event": ALERT_ID,
        "source_partition": "saved runtime weapon-alert evidence; source times 11.000-12.529 seconds",
        "source_label_or_model_output": "Actual saved YOLO weapon alert; predicted class knife, confidence about 0.37",
        "ground_truth_status": "not adjudicated; detector output is not ground truth",
        "case_note": "Four chronological saved evidence JPEGs from a separate GuardAId alert. The visible detector box and knife label are fallible predictions; the object is not adjudicated.",
        "alert_id": ALERT_ID,
        "source_workspace_relative_media": rel_media,
        "copied_media": copied_media,
        "copied_media_sha256": source_hashes,
        "copied_annotations": [],
        "excluded_pre_freeze_media_sha256": current_hashes,
    })
    metadata_path.write_text(json.dumps(metadata, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    manifest_path = ROOT / "VLM Fine-tune" / "case_manifest.csv"
    with manifest_path.open("r", encoding="utf-8-sig", newline="") as source:
        reader = csv.DictReader(source)
        fieldnames = reader.fieldnames
        rows = list(reader)
    row = next(item for item in rows if item["case_id"] == "ACC-VA-01")
    row.update({
        "source_partition": metadata["source_partition"],
        "source_event": ALERT_ID,
        "alert_id": ALERT_ID,
        "source_label_or_model_output": metadata["source_label_or_model_output"],
        "ground_truth_status": metadata["ground_truth_status"],
        "media_count": str(len(new_names)),
        "annotation_count": "0",
        "media_files": ";".join(copied_media),
        "sha256": ";".join(source_hashes),
    })
    with manifest_path.open("w", encoding="utf-8-sig", newline="") as target:
        writer = csv.DictWriter(target, fieldnames=fieldnames, extrasaction="raise")
        writer.writeheader()
        writer.writerows(rows)

    readme = ROOT / "VLM Fine-tune" / "README.md"
    note = (
        "\n## Pre-freeze visual audit\n\n"
        "- `ACC-VA-01` was replaced before prompt evaluation because its first saved copy contained a prominent stock-media watermark. The old copied evidence is retained under `Excluded Before Freeze/ACC-VA-01-watermarked`; source files were not changed. The active case now uses four chronological, unwatermarked photos from a different saved GuardAId weapon alert. Its detector output remains unadjudicated.\n"
    )
    current = readme.read_text(encoding="utf-8")
    if "## Pre-freeze visual audit" not in current:
        readme.write_text(current.rstrip() + "\n" + note, encoding="utf-8")

    archived_hashes = [sha256(path) for path in sorted(ARCHIVE.glob("*.jpg"))]
    if archived_hashes != current_hashes:
        raise RuntimeError("The archived pre-freeze media did not pass its hash check")
    print(f"[replaced] ACC-VA-01 now has {len(new_names)} photos; old copy archived with SHA-256 verified.", flush=True)


if __name__ == "__main__":
    main()
