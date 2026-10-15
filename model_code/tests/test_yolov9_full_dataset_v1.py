import csv
import json
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest import mock

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import yolov9_full_dataset_v1 as recipe  # noqa: E402


class FullDatasetV1Tests(unittest.TestCase):
    def _image(self, path: Path, seed: int) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        color = ((seed * 37) % 251, (seed * 71 + 17) % 253, (seed * 113 + 29) % 255)
        Image.new("RGB", (32, 32), color).save(path)
        return path

    def _yolo(self, root: Path, dataset: str, split: str, name: str, label: str, seed: int) -> Path:
        base = root / dataset
        if dataset == "WeaponDataset.v11i.yolov9":
            image = self._image(base / split / "images" / f"{name}.jpg", seed)
            label_path = base / split / "labels" / f"{name}.txt"
        else:
            split_dir = () if split == "unsplit" else (split,)
            image = self._image(base / "images" / Path(*split_dir) / f"{name}.jpg", seed)
            label_path = base / "labels" / Path(*split_dir) / f"{name}.txt"
        label_path.parent.mkdir(parents=True, exist_ok=True)
        label_path.write_text(label, encoding="utf-8")
        return image

    def _xml(self, root: Path, image_rel: str, xml_rel: str, category: str, seed: int) -> Path:
        image = self._image(root / image_rel, seed)
        xml = ET.Element("annotation")
        size = ET.SubElement(xml, "size")
        ET.SubElement(size, "width").text = "32"
        ET.SubElement(size, "height").text = "32"
        ET.SubElement(size, "depth").text = "3"
        obj = ET.SubElement(xml, "object")
        ET.SubElement(obj, "name").text = category
        bounds = ET.SubElement(obj, "bndbox")
        for key, value in (("xmin", 8), ("ymin", 8), ("xmax", 24), ("ymax", 24)):
            ET.SubElement(bounds, key).text = str(value)
        xml_path = root / xml_rel
        xml_path.parent.mkdir(parents=True, exist_ok=True)
        ET.ElementTree(xml).write(xml_path, encoding="utf-8")
        return image

    def _make_source_tree(self, root: Path) -> Path:
        data = root / "Weapon Detection Model Dataset"
        # Dangerous Items preserves the native three-way split.
        self._yolo(data, "Dangerous Items", "train", "danger-knife", "1 0.5 0.5 0.5 0.5\n", 1)
        duplicate = self._yolo(data, "Dangerous Items", "test", "shared-gun", "4 0.5 0.5 0.5 0.5\n", 2)
        self._yolo(data, "Dangerous Items", "val", "baseball-only", "2 0.5 0.5 0.4 0.4\n", 3)
        # Four negative frames from one ShanghaiTech video stay in train.
        manifest = data / "Dangerous Items" / "shanghaitech_negative_manifest.csv"
        manifest.parent.mkdir(parents=True, exist_ok=True)
        with manifest.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=("filename", "split", "video_id"))
            writer.writeheader()
            for index in range(4):
                name = f"sh-frame-{index}.jpg"
                self._yolo(data, "Dangerous Items", "train", Path(name).stem, "", 10 + index)
                writer.writerow({"filename": name, "split": "train", "video_id": "SH-video-1"})

        # Auxiliary YOLO datasets: class 0 is a person; class 1 is the object.
        for index in range(12):
            self._yolo(data, "Dataset", "unsplit", f"long-{index}", "0 0.5 0.5 0.7 0.8\n1 0.5 0.5 0.2 0.3\n", 20 + index)
            self._yolo(data, "Knife_Dataset/Knife_Dataset", "unsplit", f"knife-{index}", "0 0.5 0.5 0.7 0.8\n1 0.5 0.5 0.2 0.3\n", 50 + index)

        # V11 has 0=knife, 1=long gun, 2=other, 3=pistol. Every sample
        # containing class 2 is excluded in its entirety.
        for index in range(16):
            cls = index % 2
            self._yolo(data, "WeaponDataset.v11i.yolov9", "train", f"v11-train-{index}", f"{cls} 0.5 0.5 0.3 0.3\n", 80 + index)
        self._yolo(data, "WeaponDataset.v11i.yolov9", "train", "v11-other-train", "2 0.5 0.5 0.3 0.3\n", 99)
        self._yolo(data, "WeaponDataset.v11i.yolov9", "train", "v11-mixed-other-train", "0 0.3 0.5 0.2 0.3\n2 0.7 0.5 0.2 0.3\n", 98)
        self._yolo(data, "WeaponDataset.v11i.yolov9", "valid", "v11-valid", "3 0.5 0.5 0.3 0.3\n", 100)
        self._yolo(data, "WeaponDataset.v11i.yolov9", "valid", "v11-other-valid", "2 0.5 0.5 0.3 0.3\n", 101)
        self._yolo(data, "WeaponDataset.v11i.yolov9", "valid", "v11-mixed-other-valid", "3 0.3 0.5 0.2 0.3\n2 0.7 0.5 0.2 0.3\n", 102)

        # OD SOHAS: numbered frames are grouped; the overlap with native test
        # is excluded from trainval rather than split across the boundary.
        sohas = "OD-WeaponDetection-master/Weapons and similar handled objects/Sohas_weapon-Detection"
        for index, (prefix, category) in enumerate((("SeqA", "knife"), ("SeqB", "pistol"), ("SeqC", "billete"), ("SeqD", "billete"), ("SeqE", "knife"), ("SeqF", "pistol"), ("SeqG", "billete"), ("Overlap", "knife"))):
            self._xml(data, f"{sohas}/images/{prefix}001.jpg", f"{sohas}/annotations/xmls/{prefix}001.xml", category, 120 + index)
        self._xml(data, f"{sohas}/images_test/Overlap002.jpg", f"{sohas}/annotations_test/xmls/Overlap002.xml", "knife", 140)
        self._xml(data, f"{sohas}/images_test/Native001.jpg", f"{sohas}/annotations_test/xmls/Native001.xml", "pistol", 141)

        # OD Knife Detection and OD Pistol Detection are unsplit and use 80/10/10.
        for index in range(12):
            self._xml(data, f"OD-WeaponDetection-master/Knife_detection/Images/od-knife-{index}.jpg", f"OD-WeaponDetection-master/Knife_detection/annotations/od-knife-{index}.xml", "knife", 160 + index)
            self._xml(data, f"OD-WeaponDetection-master/Pistol detection/Weapons/od-pistol-{index}.jpg", f"OD-WeaponDetection-master/Pistol detection/xmls/od-pistol-{index}.xml", "pistol", 190 + index)

        # An exact source duplicate must not survive in a second split.
        duplicate_image = data / "OD-WeaponDetection-master" / "Pistol detection" / "Weapons" / "shared-gun.jpg"
        duplicate_image.parent.mkdir(parents=True, exist_ok=True)
        duplicate_image.write_bytes(duplicate.read_bytes())
        self._xml(data, "OD-WeaponDetection-master/Pistol detection/Weapons/shared-gun.jpg", "OD-WeaponDetection-master/Pistol detection/xmls/shared-gun.xml", "pistol", 240)
        duplicate_image.write_bytes(duplicate.read_bytes())
        return data

    def test_fixed_recipe_drops_other_and_non_target_only_samples_and_blocks_leakage(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[2] / "tmp") as directory:
            temp = Path(directory)
            data_root = self._make_source_tree(temp)
            output_root = temp / "runs"
            with mock.patch.object(recipe, "DATA_ROOT", data_root):
                report = recipe.build_dataset(output_root=output_root)
                again = recipe.build_dataset(output_root=output_root)

            self.assertEqual(report["class_names"], ["knife", "gun"])
            self.assertEqual(report["v11_other_samples_dropped"], 4)
            self.assertEqual(report["excluded_reasons"]["v11_other_class_sample_dropped"], 4)
            self.assertIn("non_target_weapon_sample_dropped", report["excluded_reasons"])
            self.assertTrue(report["source_split_rules"]["shanghaitech_video_split"]["valid"])
            self.assertGreater(report["duplicate_groups_removed"], 0)

            with Path(report["manifest"]).open("r", encoding="utf-8-sig", newline="") as stream:
                manifest = list(csv.DictReader(stream))
            image_hash_splits = {}
            for row in manifest:
                image_hash_splits.setdefault(row["pixel_sha256"], set()).add(row["split"])
                self.assertTrue(set(row["classes"].split(",")) <= {"0", "1"} if row["classes"] else True)
                if row["source"] == "weapon_dataset_v11":
                    self.assertNotIn(Path(row["source_image"]).stem, {"v11-other-train", "v11-other-valid", "v11-mixed-other-train", "v11-mixed-other-valid"})
            self.assertTrue(all(len(splits) == 1 for splits in image_hash_splits.values()))

            excluded = Path(report["excluded_csv"]).read_text(encoding="utf-8-sig")
            self.assertIn("v11-other-train", excluded)
            self.assertIn("v11-other-valid", excluded)
            self.assertIn("v11-mixed-other-train", excluded)
            self.assertIn("v11-mixed-other-valid", excluded)
            self.assertNotIn("v11-other-train", {Path(row["source_image"]).stem for row in manifest})

            # A second build must yield the same fingerprint and reuse cache.
            self.assertEqual(report["fingerprint"], again["fingerprint"])


if __name__ == "__main__":
    unittest.main()
