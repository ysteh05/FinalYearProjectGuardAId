"""Read-only hooks injected into the unmodified upstream YOLOv9 process."""

from __future__ import annotations

import csv
import json
import os
import random
import shutil
import sys
import time
import traceback
import weakref
from pathlib import Path
from typing import Any


_STATE_PATH: Path | None = None
_EVENT_PATH: Path | None = None
_CONTROL_PATH: Path | None = None
_RUN_CONFIG: dict[str, Any] = {}
_EPOCH_STARTED = time.time()
_CURRENT_EPOCH = 0
_LAST_SAVED_EPOCH = -1
_LAST_STEP = 0
_LAST_HOOK = 0.0
_CALLBACK_ORIGINAL = None
_SCALER_REF = None
_SCHEDULERS = []
_RESUME_STATE: dict[str, Any] | None = None
_VALIDATION_IMAGES_DONE = 0
_NEGATIVE_IMAGE_CACHE: dict[str, dict[str, str]] = {}
_FPR_TRACKER: dict[str, Any] = {}
_LAST_FPR_RECORD: dict[str, Any] = {}
_LAST_VALIDATION_RESULTS: list[float] | None = None


def f_beta_score(precision: float, recall: float, beta: float = 0.5) -> float:
    beta_squared = beta * beta
    denominator = beta_squared * precision + recall
    if denominator <= 0:
        return 0.0
    return (1 + beta_squared) * precision * recall / denominator


def validation_score_summary(results: Any) -> dict[str, float]:
    values = [_scalar_float(value) for value in list(results)[:4]]
    if len(values) < 4:
        raise ValueError("YOLO validation results must contain precision, recall, mAP50 and mAP50-95")
    precision, recall, map50, map50_95 = values
    f05 = f_beta_score(precision, recall, 0.5)
    fitness = 0.1 * map50 + 0.9 * map50_95
    return {
        "precision": precision,
        "recall": recall,
        "f0_5": f05,
        "fitness": fitness,
        "early_stop_benchmark": 0.5 * f05 + 0.5 * fitness,
        "map50": map50,
        "map50_95": map50_95,
    }


def _scalar_float(value: Any) -> float:
    """Accept Python numbers and single-element NumPy arrays or tensors."""
    if hasattr(value, "detach"):
        value = value.detach().cpu()
    if hasattr(value, "item"):
        value = value.item()
    return float(value)


def _jsonable(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if hasattr(value, "detach"):
        try:
            return value.detach().cpu().tolist()
        except Exception:
            pass
    if hasattr(value, "item"):
        try:
            return value.item()
        except Exception:
            pass
    if hasattr(value, "tolist"):
        return _jsonable(value.tolist())
    return value


def _read_state() -> dict[str, Any]:
    if _STATE_PATH and _STATE_PATH.is_file():
        try:
            return json.loads(_STATE_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _replace_state_file(temporary: Path, destination: Path) -> None:
    # The local dashboard reads this file while the training subprocess
    # updates it. Windows can reject replace() for the few milliseconds that
    # a reader has the destination open, so retry transient sharing errors.
    for attempt in range(50):
        try:
            os.replace(temporary, destination)
            return
        except PermissionError:
            if attempt == 49:
                raise
            time.sleep(0.05)


def _write_state(update: dict[str, Any], event: str | None = None) -> None:
    if not _STATE_PATH:
        return
    state = _read_state()
    state.update(_jsonable(update))
    state["updated_at"] = time.time()
    temporary = _STATE_PATH.with_name(_STATE_PATH.name + f".{os.getpid()}.tmp")
    temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        _replace_state_file(temporary, _STATE_PATH)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    if event and _EVENT_PATH:
        record = {"event": event, "time": time.time(), **_jsonable(update)}
        with _EVENT_PATH.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")
            stream.flush()


def _save_resume_state(scaler_state: dict[str, Any] | None) -> None:
    if not _RUN_CONFIG.get("resume_state_path"):
        return
    try:
        import numpy as np
        import torch

        snapshot: dict[str, Any] = {
            "python_rng_state": random.getstate(),
            "numpy_rng_state": np.random.get_state(),
            "torch_rng_state": torch.get_rng_state(),
            "torch_cuda_rng_states": torch.cuda.get_rng_state_all() if torch.cuda.is_available() else [],
            "amp_scaler_state": scaler_state,
            "scheduler_states": [scheduler.state_dict() for scheduler in _SCHEDULERS],
        }
        torch.save(snapshot, _RUN_CONFIG["resume_state_path"])
    except Exception as exc:
        _write_state({"resume_sidecar_warning": str(exc)}, "resume_sidecar_warning")


def _restore_resume_state() -> None:
    global _RESUME_STATE
    raw_path = os.environ.get("YOLOV9_RESUME_STATE")
    if not raw_path or _RESUME_STATE is not None:
        return
    path = Path(raw_path)
    if not path.is_file():
        return
    try:
        import torch

        _RESUME_STATE = torch.load(path, map_location="cpu", weights_only=False)
        random.setstate(_RESUME_STATE["python_rng_state"])
        import numpy as np

        np.random.set_state(_RESUME_STATE["numpy_rng_state"])
        torch.set_rng_state(_RESUME_STATE["torch_rng_state"])
        states = _RESUME_STATE.get("torch_cuda_rng_states") or []
        if states and torch.cuda.is_available():
            torch.cuda.set_rng_state_all(states)
    except Exception as exc:
        _write_state({"resume_rng_warning": str(exc)}, "resume_rng_warning")


def _emit_validation_rows(args: tuple[Any, ...]) -> None:
    global _VALIDATION_IMAGES_DONE
    if not _RUN_CONFIG.get("capture_validation_predictions"):
        return
    # Upstream val_dual invokes (batch_i, im, targets, paths, shapes, preds)
    # after NMS. Predictions remain in letterboxed input coordinates here.
    if len(args) < 6:
        return
    batch_i, images, targets, paths, shapes, predictions = args[:6]
    try:
        import torch
        from utils.general import scale_boxes, xywh2xyxy

        image_h, image_w = int(images.shape[2]), int(images.shape[3])
        output_path = Path(_RUN_CONFIG["validation_predictions_path"])
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("a", encoding="utf-8") as stream:
            for image_index, image_path in enumerate(paths):
                prediction = predictions[image_index].detach().clone()
                shape, ratio_pad = shapes[image_index]
                if len(prediction):
                    scale_boxes((image_h, image_w), prediction[:, :4], shape, ratio_pad)
                    pred_rows = prediction[:, :6].detach().cpu().tolist()
                else:
                    pred_rows = []
                image_targets = targets[targets[:, 0] == image_index, 1:].detach().clone()
                if len(image_targets):
                    target_boxes = image_targets[:, 1:5].clone()
                    target_boxes = xywh2xyxy(target_boxes)
                    scale_boxes((image_h, image_w), target_boxes, shape, ratio_pad)
                    target_rows = [
                        [int(row[0]), *[float(v) for v in box]]
                        for row, box in zip(image_targets.cpu().tolist(), target_boxes.cpu().tolist())
                    ]
                else:
                    target_rows = []
                stream.write(json.dumps({"image": str(Path(image_path).resolve()), "predictions": pred_rows, "targets": target_rows}) + "\n")
        _VALIDATION_IMAGES_DONE += int(images.shape[0])
        _write_state({"validation_images_done": _VALIDATION_IMAGES_DONE, "evaluation_images_done": _VALIDATION_IMAGES_DONE, "evaluation_batch": int(batch_i + 1)}, "validation_batch")
    except Exception as exc:
        _write_state({"validation_capture_warning": str(exc), "validation_capture_traceback": traceback.format_exc(limit=2)}, "validation_capture_warning")


def _canonical_path(path: str) -> str:
    return os.path.normcase(os.path.abspath(path))


def _negative_images_for_split(split: str) -> dict[str, str]:
    if split in _NEGATIVE_IMAGE_CACHE:
        return _NEGATIVE_IMAGE_CACHE[split]
    manifest = Path(str(_RUN_CONFIG.get("fpr_manifest_path", "")))
    aliases: dict[str, str] = {}
    if manifest.is_file():
        with manifest.open("r", encoding="utf-8-sig", newline="") as stream:
            for row in csv.DictReader(stream):
                is_negative = str(row.get("negative", "")).strip().casefold() in {"true", "1", "yes"}
                if row.get("split") != split or not is_negative:
                    continue
                primary = str(row.get("image", "")).strip()
                fallback = str(row.get("source_image", "")).strip()
                identity = _canonical_path(primary or fallback)
                for value in (primary, fallback):
                    if value and value != "(empty)":
                        aliases[_canonical_path(value)] = identity
    _NEGATIVE_IMAGE_CACHE[split] = aliases
    return aliases


def _track_negative_false_positives(args: tuple[Any, ...]) -> None:
    if not _RUN_CONFIG.get("track_negative_fpr") or len(args) < 6:
        return
    try:
        _batch_i, _images, _targets, paths, _shapes, predictions = args[:6]
        negative_paths: dict[str, str] = _FPR_TRACKER["negative_paths"]
        threshold = float(_RUN_CONFIG.get("fpr_confidence_threshold", 0.25))
        for image_index, image_path in enumerate(paths):
            canonical = _canonical_path(str(image_path))
            identity = negative_paths.get(canonical)
            if identity is None or identity in _FPR_TRACKER["seen"]:
                continue
            _FPR_TRACKER["seen"].add(identity)
            pred = predictions[image_index]
            if len(pred):
                selected = pred[:, 4] >= threshold
                if bool(selected.any()):
                    _FPR_TRACKER["images_with_false_positive"].add(identity)
                    _FPR_TRACKER["false_positive_boxes"] += int(selected.sum().item())
    except Exception as exc:
        _write_state({"negative_fpr_tracking_warning": str(exc), "negative_fpr_tracking_traceback": traceback.format_exc(limit=2)}, "negative_fpr_tracking_warning")


def _on_event(hook: str, args: tuple[Any, ...], kwargs: dict[str, Any], callbacks: Any) -> None:
    global _CURRENT_EPOCH, _EPOCH_STARTED, _LAST_SAVED_EPOCH, _LAST_STEP, _LAST_HOOK, _VALIDATION_IMAGES_DONE, _FPR_TRACKER, _LAST_FPR_RECORD
    now = time.time()
    if hook == "on_train_start":
        _EPOCH_STARTED = now
        _write_state({"stage": "training", "stage_label": "正在训练", "trainer_started_at": now}, "train_started")
    elif hook == "on_train_epoch_start":
        _CURRENT_EPOCH += 1
        stop_after_epoch = _RUN_CONFIG.get("stop_after_epoch")
        if stop_after_epoch is not None and _LAST_SAVED_EPOCH >= int(stop_after_epoch):
            # The upstream trainer checks callbacks.stop_training after a batch.
            # Exit here, before the next epoch can consume even one batch.
            _write_state(
                {
                    "budget_finalization_requested": True,
                    "budget_finalization_epoch": _LAST_SAVED_EPOCH,
                    "stage": "finalizing",
                    "stage_label": f"已完整保存 Epoch {_LAST_SAVED_EPOCH}，在下一轮首个 batch 前停止并进行最终评估",
                },
                "configured_epoch_limit_reached_before_batch",
            )
            raise SystemExit(0)
        _EPOCH_STARTED = now
        _LAST_STEP = 0
        _write_state({"stage": "training", "stage_label": f"训练 Epoch {_CURRENT_EPOCH}", "epoch": _CURRENT_EPOCH, "epoch_elapsed_seconds": 0}, "epoch_started")
    elif hook == "on_train_batch_end" and len(args) >= 6:
        ni, images, targets, paths, losses = args[1], args[2], args[3], args[4], args[5]
        nb = max(1, int(_RUN_CONFIG.get("train_batches_per_epoch", 1)))
        step = int(ni) + 1
        batch = step % nb or nb
        if batch != _LAST_STEP or now - _LAST_HOOK >= 1.0:
            _LAST_STEP, _LAST_HOOK = batch, now
            loss_values = [_scalar_float(value) for value in losses]
            elapsed = max(0.1, now - _EPOCH_STARTED)
            _write_state(
                {
                    "stage": "training",
                    "epoch": max(1, _CURRENT_EPOCH),
                    "epochs": int(_RUN_CONFIG.get("epochs", 1)),
                    "batch": batch,
                    "batches_per_epoch": nb,
                    "epoch_progress": min(1.0, batch / nb),
                    "samples_processed_total": (max(0, _CURRENT_EPOCH - 1) * nb * int(_RUN_CONFIG.get("batch_size", 1))) + (batch - 1) * int(_RUN_CONFIG.get("batch_size", 1)) + int(images.shape[0]),
                    "current_loss": {"box": loss_values[0], "cls": loss_values[1], "dfl": loss_values[2]},
                    "epoch_elapsed_seconds": elapsed,
                    "estimated_epoch_seconds": elapsed / max(1, batch) * nb,
                    "gpu_memory_gb": _gpu_memory_gb(),
                },
                "train_batch",
            )
        _check_control(callbacks)
    elif hook == "on_fit_epoch_end" and len(args) >= 4:
        values, epoch, best_fitness, fitness = args[:4]
        values = [_scalar_float(x) for x in values]
        metric = {
            "epoch": int(epoch) + 1,
            "fitness": _scalar_float(fitness),
            "best_fitness": _scalar_float(best_fitness),
            "train_loss": {"box": values[0], "cls": values[1], "dfl": values[2]},
            "precision": values[3],
            "recall": values[4],
            "map50": values[5],
            "map50_95": values[6],
            "learning_rate": values[10:] if len(values) > 10 else [],
            "epoch_seconds": max(0.0, now - _EPOCH_STARTED),
        }
        if _RUN_CONFIG.get("early_stop_metric") == "f05_fitness_mean":
            metric["f0_5"] = f_beta_score(metric["precision"], metric["recall"], 0.5)
            metric["early_stop_benchmark"] = 0.5 * metric["f0_5"] + 0.5 * metric["fitness"]
        if _RUN_CONFIG.get("track_negative_fpr") and _LAST_FPR_RECORD.get("split") == "val":
            metric.update({
                "val_fpr_threshold": _LAST_FPR_RECORD["confidence_threshold"],
                "val_negative_images": _LAST_FPR_RECORD["negative_images"],
                "val_negative_images_evaluated": _LAST_FPR_RECORD["negative_images_evaluated"],
                "val_false_positive_images": _LAST_FPR_RECORD["false_positive_images"],
                "val_false_positive_rate": _LAST_FPR_RECORD["false_positive_rate"],
                "val_false_positive_boxes": _LAST_FPR_RECORD["false_positive_boxes"],
            })
        _append_csv(metric)
        _write_state({"stage": "validation", "stage_label": f"Epoch {int(epoch)+1} 验证完成", "epoch": int(epoch) + 1, "last_epoch_metrics": metric}, "epoch_metrics")
    elif hook == "on_model_save" and len(args) >= 5:
        last, epoch, _final_epoch, best_fitness, fitness = args[:5]
        _LAST_SAVED_EPOCH = int(epoch) + 1
        resumable_path = _RUN_CONFIG.get("resume_checkpoint_path")
        full_checkpoint_saved = not resumable_path
        if resumable_path:
            try:
                target = Path(resumable_path)
                target.parent.mkdir(parents=True, exist_ok=True)
                temporary = target.with_name(target.name + f".{os.getpid()}.tmp")
                shutil.copy2(last, temporary)
                os.replace(temporary, target)
                full_checkpoint_saved = True
            except Exception as exc:
                _write_state({"full_checkpoint_copy_warning": str(exc)}, "full_checkpoint_copy_warning")
        epoch_checkpoint_dir = _RUN_CONFIG.get("epoch_checkpoint_dir")
        if epoch_checkpoint_dir:
            try:
                snapshot_dir = Path(epoch_checkpoint_dir)
                snapshot_dir.mkdir(parents=True, exist_ok=True)
                snapshot = snapshot_dir / f"epoch_{_LAST_SAVED_EPOCH:02d}.pt"
                temporary = snapshot.with_name(snapshot.name + f".{os.getpid()}.tmp")
                shutil.copy2(last, temporary)
                os.replace(temporary, snapshot)
            except Exception as exc:
                _write_state({"epoch_checkpoint_warning": str(exc)}, "epoch_checkpoint_warning")
        scaler_state = _snapshot_scaler()
        _save_resume_state(scaler_state)
        checkpoint_update = {"checkpoint_path": str(last), "checkpoint_saved_epoch": _LAST_SAVED_EPOCH, "checkpoint_saved_at": now, "best_fitness": _scalar_float(best_fitness), "epoch_fitness": _scalar_float(fitness)}
        if full_checkpoint_saved:
            checkpoint_update["full_checkpoint_saved_epoch"] = _LAST_SAVED_EPOCH
        _write_state(checkpoint_update, "checkpoint_saved")
        control = {}
        if _CONTROL_PATH and _CONTROL_PATH.is_file():
            try:
                control = json.loads(_CONTROL_PATH.read_text(encoding="utf-8"))
            except Exception:
                control = {}
        if control.get("command") in {"pause", "budget_finalize"} and not full_checkpoint_saved:
            _write_state({"pause_blocked_checkpoint_copy_failed": True, "stage_label": "无法保存独立恢复 checkpoint；本轮继续运行"}, "pause_blocked")
        else:
            _check_control(callbacks, checkpoint_saved=True)
    elif hook == "on_val_start":
        _VALIDATION_IMAGES_DONE = 0
        _LAST_FPR_RECORD = {}
        split = _RUN_CONFIG.get("evaluation_split", "val")
        _FPR_TRACKER = {
            "split": split,
            "negative_paths": _negative_images_for_split(split),
            "seen": set(),
            "images_with_false_positive": set(),
            "false_positive_boxes": 0,
        }
        _write_state({"stage": "evaluation", "stage_label": f"正在评估 {split}", "evaluation_split": split, "evaluation_started_at": now, "validation_images_done": 0}, "evaluation_started")
        if _RUN_CONFIG.get("capture_validation_predictions"):
            prediction_path = Path(_RUN_CONFIG["validation_predictions_path"])
            prediction_path.parent.mkdir(parents=True, exist_ok=True)
            prediction_path.write_text("", encoding="utf-8")
    elif hook == "on_val_batch_end":
        if args:
            _track_negative_false_positives(args)
            _emit_validation_rows(args)
            if not _RUN_CONFIG.get("capture_validation_predictions"):
                batch_i, images = args[0], args[1]
                _VALIDATION_IMAGES_DONE += int(images.shape[0])
                _write_state({"stage": "evaluation", "evaluation_batch": int(batch_i) + 1, "validation_images_done": _VALIDATION_IMAGES_DONE, "evaluation_images_done": _VALIDATION_IMAGES_DONE, "gpu_memory_gb": _gpu_memory_gb()}, "evaluation_batch")
    elif hook == "on_val_end" and len(args) >= 10:
        nt, _tp, _fp, precision, recall, f1, ap, ap50, ap_class, _confusion = args[:10]
        names = _RUN_CONFIG.get("class_names", [])
        per_class = {}
        for index, cls in enumerate(ap_class):
            cls_id = int(cls)
            name = names[cls_id] if cls_id < len(names) else str(cls_id)
            per_class[name] = {
                "images": int(_RUN_CONFIG.get("evaluation_image_count", 0)),
                "instances": int(nt[cls_id]),
                "precision": _scalar_float(precision[index]),
                "recall": _scalar_float(recall[index]),
                "f1": _scalar_float(f1[index]),
                "map50": _scalar_float(ap50[index]),
                "map50_95": _scalar_float(ap[index].mean()),
            }
        _write_state({"evaluation_per_class": per_class}, "evaluation_per_class")
        if _RUN_CONFIG.get("track_negative_fpr"):
            split = str(_FPR_TRACKER.get("split", _RUN_CONFIG.get("evaluation_split", "val")))
            negative_count = len(set(_FPR_TRACKER.get("negative_paths", {}).values()))
            evaluated_count = len(_FPR_TRACKER.get("seen", set()))
            false_positive_images = len(_FPR_TRACKER.get("images_with_false_positive", set()))
            record = {
                "split": split,
                "confidence_threshold": float(_RUN_CONFIG.get("fpr_confidence_threshold", 0.25)),
                "negative_images": negative_count,
                "negative_images_evaluated": evaluated_count,
                "false_positive_images": false_positive_images,
                "false_positive_rate": false_positive_images / negative_count if negative_count else None,
                "false_positive_boxes": int(_FPR_TRACKER.get("false_positive_boxes", 0)),
            }
            _LAST_FPR_RECORD = record
            _write_state({"negative_fpr_record": record}, "negative_fpr_record")
            if evaluated_count != negative_count:
                _write_state({"negative_fpr_tracking_warning": f"Evaluated {evaluated_count} of {negative_count} negative images for {split}"}, "negative_fpr_denominator_warning")


def _append_csv(metric: dict[str, Any]) -> None:
    if not _RUN_CONFIG.get("metrics_csv"):
        return
    path = Path(_RUN_CONFIG["metrics_csv"])
    path.parent.mkdir(parents=True, exist_ok=True)
    flat = {"epoch": metric["epoch"], "fitness": metric["fitness"], "best_fitness": metric["best_fitness"], "train/box_loss": metric["train_loss"]["box"], "train/cls_loss": metric["train_loss"]["cls"], "train/dfl_loss": metric["train_loss"]["dfl"], "metrics/precision": metric["precision"], "metrics/recall": metric["recall"], "metrics/mAP50": metric["map50"], "metrics/mAP50-95": metric["map50_95"], "lr": json.dumps(metric["learning_rate"]), "epoch_seconds": metric["epoch_seconds"]}
    flat.update({
        "val_fpr_threshold": metric.get("val_fpr_threshold"),
        "val_negative_images": metric.get("val_negative_images"),
        "val_negative_images_evaluated": metric.get("val_negative_images_evaluated"),
        "val_false_positive_images": metric.get("val_false_positive_images"),
        "val_false_positive_rate": metric.get("val_false_positive_rate"),
        "val_false_positive_boxes": metric.get("val_false_positive_boxes"),
    })
    if "f0_5" in metric:
        flat["val_f0_5"] = metric["f0_5"]
        flat["early_stop_benchmark"] = metric["early_stop_benchmark"]
    is_new = not path.exists()
    with path.open("a", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(flat))
        if is_new:
            writer.writeheader()
        writer.writerow(flat)


def _snapshot_scaler() -> dict[str, Any] | None:
    try:
        scaler = _SCALER_REF() if _SCALER_REF else None
        return scaler.state_dict() if scaler is not None else None
    except Exception:
        return None


def _check_control(callbacks: Any, checkpoint_saved: bool = False) -> None:
    if not _CONTROL_PATH or not _CONTROL_PATH.is_file():
        return
    try:
        control = json.loads(_CONTROL_PATH.read_text(encoding="utf-8"))
    except Exception:
        return
    command = control.get("command")
    if command in {"pause", "budget_finalize"}:
        # Upstream calls this hook after writing last.pt for the just-finished
        # epoch. If the request arrives mid-epoch, wait for that save callback.
        if checkpoint_saved:
            callbacks.stop_training = True
            reason = "budget_finalize" if command == "budget_finalize" else "user_pause"
            label = "已保存完整 epoch，进入最终评估" if command == "budget_finalize" else "已在 epoch 边界安全暂停"
            _write_state({"stage": "paused", "status": "paused", "pause_reason": reason, "stage_label": label, "paused_epoch": _LAST_SAVED_EPOCH}, "safe_pause")
    elif command == "stop":
        callbacks.stop_training = True
        _write_state({"stage": "stopping", "stage_label": "正在安全停止；保留最近完整 checkpoint"}, "stop_requested")
    elif checkpoint_saved and _RUN_CONFIG.get("stop_after_epoch") is not None:
        stop_after_epoch = int(_RUN_CONFIG["stop_after_epoch"])
        if _LAST_SAVED_EPOCH >= stop_after_epoch:
            callbacks.stop_training = True
            _write_state(
                {
                    "budget_finalization_requested": True,
                    "budget_finalization_epoch": _LAST_SAVED_EPOCH,
                    "stage": "finalizing",
                    "stage_label": f"已完整保存 Epoch {_LAST_SAVED_EPOCH}，按指定轮数停止并进行最终评估",
                },
                "configured_epoch_limit_reached",
            )


def _gpu_memory_gb() -> float | None:
    try:
        import torch

        if torch.cuda.is_available():
            return round(torch.cuda.memory_reserved() / (1024**3), 2)
    except Exception:
        return None
    return None


def _patch_resume_runtime() -> None:
    global _SCHEDULERS
    try:
        import torch
        import utils.torch_utils as torch_utils

        resume_epoch = int(os.environ.get("YOLOV9_RESUME_EPOCH", "0"))
        original_lambda = torch.optim.lr_scheduler.LambdaLR

        class ResumeAwareLambdaLR(original_lambda):
            def __init__(self, optimizer, lr_lambda, *args, **kwargs):
                self._workspace_resume_epoch = resume_epoch
                if resume_epoch > 0:
                    for group in optimizer.param_groups:
                        group.setdefault("initial_lr", group["lr"])
                    kwargs["last_epoch"] = resume_epoch - 1
                super().__init__(optimizer, lr_lambda, *args, **kwargs)
                _SCHEDULERS.append(self)

        torch.optim.lr_scheduler.LambdaLR = ResumeAwareLambdaLR
        original_smart_resume = torch_utils.smart_resume

        def smart_resume_with_scheduler(*args, **kwargs):
            result = original_smart_resume(*args, **kwargs)
            _best, start_epoch, _epochs = result
            if start_epoch > 0:
                for scheduler in _SCHEDULERS:
                    scheduler.last_epoch = int(start_epoch)
                    values = []
                    for group, base_lr, multiplier in zip(scheduler.optimizer.param_groups, scheduler.base_lrs, scheduler.lr_lambdas):
                        lr = base_lr * multiplier(int(start_epoch))
                        group["lr"] = lr
                        values.append(lr)
                    scheduler._last_lr = values
            saved_states = (_RESUME_STATE or {}).get("scheduler_states", [])
            if saved_states and len(saved_states) == len(_SCHEDULERS):
                for scheduler, saved_state in zip(_SCHEDULERS, saved_states):
                    scheduler.load_state_dict(saved_state)
            return result

        torch_utils.smart_resume = smart_resume_with_scheduler

        original_early_stopping = torch_utils.EarlyStopping
        uses_f05_benchmark = _RUN_CONFIG.get("early_stop_metric") == "f05_fitness_mean"

        class ResumeAwareEarlyStopping(original_early_stopping):
            def __init__(self, patience=30):
                super().__init__(patience)
                if resume_epoch <= 0:
                    return
                metrics_path = _RUN_CONFIG.get("metrics_csv")
                if not metrics_path or not Path(metrics_path).is_file():
                    return
                try:
                    with Path(metrics_path).open("r", encoding="utf-8-sig", newline="") as stream:
                        rows = list(csv.DictReader(stream))
                    metric_key = "early_stop_benchmark" if uses_f05_benchmark else "fitness"
                    best = max(rows, key=lambda row: float(row.get(metric_key, "-inf")))
                    self.best_fitness = float(best[metric_key])
                    self.best_epoch = int(best["epoch"]) - 1
                except Exception as exc:
                    _write_state({"early_stopping_resume_warning": str(exc)}, "early_stopping_resume_warning")

            def __call__(self, epoch, fitness):
                if not uses_f05_benchmark:
                    return super().__call__(epoch=epoch, fitness=fitness)
                if _LAST_VALIDATION_RESULTS is None:
                    raise RuntimeError("F0.5 early stopping requires the completed validation metrics for this epoch")
                summary = validation_score_summary(_LAST_VALIDATION_RESULTS)
                _write_state({"early_stop_metric": summary}, "early_stop_metric")
                return super().__call__(epoch=epoch, fitness=summary["early_stop_benchmark"])

        torch_utils.EarlyStopping = ResumeAwareEarlyStopping
    except Exception as exc:
        _write_state({"scheduler_resume_warning": str(exc)}, "scheduler_resume_warning")


def _patch_rng_and_scaler() -> None:
    global _SCALER_REF
    try:
        import torch
        import utils.general as general

        original_seed = general.init_seeds

        def seed_then_restore(*args, **kwargs):
            original_seed(*args, **kwargs)
            _restore_resume_state()

        general.init_seeds = seed_then_restore
        scaler_cls = torch.cuda.amp.GradScaler
        original_init = scaler_cls.__init__

        def scaler_init(self, *args, **kwargs):
            global _SCALER_REF
            original_init(self, *args, **kwargs)
            _SCALER_REF = weakref.ref(self)
            if _RESUME_STATE and _RESUME_STATE.get("amp_scaler_state"):
                self.load_state_dict(_RESUME_STATE["amp_scaler_state"])

        scaler_cls.__init__ = scaler_init
    except Exception as exc:
        _write_state({"amp_resume_warning": str(exc)}, "amp_resume_warning")


def install() -> None:
    global _STATE_PATH, _EVENT_PATH, _CONTROL_PATH, _RUN_CONFIG, _CALLBACK_ORIGINAL, _CURRENT_EPOCH
    # Windows data-loader children inherit the hook environment, but only the
    # training/evaluation process should publish progress or capture checkpoints.
    launch_args = getattr(sys, "orig_argv", sys.argv)[1:]
    if any(arg == "--multiprocessing-fork" or "multiprocessing.resource_tracker" in arg
           for arg in launch_args):
        return
    state_path = os.environ.get("YOLOV9_WORKSPACE_STATE")
    if not state_path:
        return
    _STATE_PATH = Path(state_path)
    _EVENT_PATH = Path(os.environ.get("YOLOV9_WORKSPACE_EVENTS", _STATE_PATH.with_name("events.jsonl")))
    _CONTROL_PATH = Path(os.environ.get("YOLOV9_WORKSPACE_CONTROL", _STATE_PATH.with_name("control.json")))
    # The upstream YOLOv9 metrics code still calls np.trapz, which was removed
    # by NumPy 2.x. Keep the compatibility aliases inside this isolated child
    # process so the shared upstream checkout and the user's Python environment
    # do not need to be modified.
    import numpy as np

    numpy_aliases = []
    if "trapz" not in vars(np):
        np.trapz = np.trapezoid
        numpy_aliases.append("trapz=trapezoid")
    if "float" not in vars(np):
        np.float = float
        numpy_aliases.append("float=builtins.float")
    _write_state({"numpy_version": np.__version__, "numpy_compatibility_aliases": numpy_aliases}, "numpy_compatibility_ready")
    pillow_aliases = []
    pillow_error = None
    try:
        from PIL import ImageFont, __version__ as pillow_version

        def legacy_getsize(self, text, *args, **kwargs):
            left, top, right, bottom = self.getbbox(text, *args, **kwargs)
            return right - left, bottom - top

        for font_class in (ImageFont.ImageFont, ImageFont.FreeTypeFont):
            if not hasattr(font_class, "getsize") and callable(getattr(font_class, "getbbox", None)):
                font_class.getsize = legacy_getsize
                pillow_aliases.append(f"{font_class.__name__}.getsize=getbbox")
        pillow_font_api_ready = all(hasattr(font_class, "getsize") for font_class in (ImageFont.ImageFont, ImageFont.FreeTypeFont))
        if not pillow_font_api_ready:
            pillow_error = "Pillow font classes do not provide a compatible getsize/getbbox API"
    except Exception as exc:
        pillow_version = "unknown"
        pillow_font_api_ready = False
        pillow_error = str(exc)
    _write_state(
        {
            "pillow_version": pillow_version,
            "pillow_compatibility_aliases": pillow_aliases,
            "pillow_font_api_ready": pillow_font_api_ready,
            "pillow_compatibility_error": pillow_error,
        },
        "pillow_compatibility_ready",
    )
    config_path = os.environ.get("YOLOV9_WORKSPACE_CONFIG")
    if config_path and Path(config_path).is_file():
        _RUN_CONFIG = json.loads(Path(config_path).read_text(encoding="utf-8"))
        _CURRENT_EPOCH = int(_RUN_CONFIG.get("start_epoch", 0))
    try:
        import torch

        # All .pt files in this run are selected/local trusted project weights.
        original_load = torch.load

        def trusted_checkpoint_load(*args, **kwargs):
            kwargs.setdefault("weights_only", False)
            return original_load(*args, **kwargs)

        torch.load = trusted_checkpoint_load
    except Exception:
        pass
    _patch_resume_runtime()
    _patch_rng_and_scaler()
    try:
        from utils.callbacks import Callbacks

        _CALLBACK_ORIGINAL = Callbacks.run

        def instrumented_run(self, hook, *args, **kwargs):
            result = _CALLBACK_ORIGINAL(self, hook, *args, **kwargs)
            _on_event(hook, args, kwargs, self)
            return result

        Callbacks.run = instrumented_run
        _write_state({"hook_runtime_ready": True, "hook_version": 2}, "runtime_hooks_ready")
    except Exception as exc:
        _write_state({"hook_runtime_error": str(exc), "hook_runtime_traceback": traceback.format_exc(limit=4)}, "runtime_hooks_error")
    try:
        import val_dual

        original_validate = val_dual.run

        def batch_limited_validate(*args, **kwargs):
            global _LAST_VALIDATION_RESULTS
            validation_batch = int(os.environ.get("YOLOV9_VALIDATION_BATCH", _RUN_CONFIG.get("batch_size", 16)))
            kwargs["batch_size"] = validation_batch
            result = original_validate(*args, **kwargs)
            if isinstance(result, tuple) and result:
                _LAST_VALIDATION_RESULTS = [_scalar_float(value) for value in list(result[0])[:4]]
                if _RUN_CONFIG.get("early_stop_metric") == "f05_fitness_mean":
                    split = str(os.environ.get("YOLOV9_EVALUATION_SPLIT", _RUN_CONFIG.get("evaluation_split", "val")))
                    try:
                        summary = validation_score_summary(_LAST_VALIDATION_RESULTS)
                        _write_state({f"{split}_score_summary": summary}, f"{split}_score_summary")
                    except (TypeError, ValueError, IndexError) as exc:
                        _write_state({"score_summary_warning": str(exc)}, "score_summary_warning")
            return result

        val_dual.run = batch_limited_validate
        _write_state({"validation_batch_limit": int(os.environ.get("YOLOV9_VALIDATION_BATCH", _RUN_CONFIG.get("batch_size", 16)))}, "validation_batch_limit")
    except Exception as exc:
        _write_state({"validation_batch_warning": str(exc)}, "validation_batch_warning")
