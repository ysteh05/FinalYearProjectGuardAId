PRAGMA foreign_keys = ON;

ALTER TABLE weapon_configs ADD COLUMN nms_candidate_floor REAL NOT NULL DEFAULT 0.001;
ALTER TABLE weapon_configs ADD COLUMN nms_iou_threshold REAL NOT NULL DEFAULT 0.45;
ALTER TABLE weapon_configs ADD COLUMN class_thresholds_json TEXT NOT NULL DEFAULT '{"sensitive":{"knife":0.2014363706111908,"long_gun":0.4412950575351715},"moderate":{"knife":0.2014363706111908,"long_gun":0.4412950575351715},"certain":{"knife":0.30376800894737244,"long_gun":0.5570604205131531}}';

INSERT OR IGNORE INTO schema_migrations(version, name, applied_at)
VALUES (2, 'three_model_inference_runtime', strftime('%Y-%m-%dT%H:%M:%fZ','now'));
