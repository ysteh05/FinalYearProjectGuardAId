BEGIN IMMEDIATE;

DROP TRIGGER description_summary_schema_insert;
DROP TRIGGER description_summary_schema_update;

CREATE TRIGGER description_summary_schema_insert BEFORE INSERT ON vlm_descriptions
BEGIN
    SELECT CASE WHEN NEW.structured_json IS NOT NULL AND NOT json_valid(NEW.structured_json) THEN RAISE(ABORT,'malformed scene JSON') END;
    SELECT CASE WHEN NEW.output_format='summary' AND NOT (
        COALESCE(json_type(NEW.structured_json)='object',0)
        AND COALESCE(json_type(NEW.structured_json,'$.description')='text' AND length(json_extract(NEW.structured_json,'$.description')) BETWEEN 1 AND 240,0)
        AND COALESCE(json_type(NEW.structured_json,'$.visible_evidence')='array',0)
        AND COALESCE(json_extract(NEW.structured_json,'$.uncertainty') IN ('low','medium','high'),0)
        AND NOT EXISTS(SELECT 1 FROM json_each(NEW.structured_json,'$.visible_evidence') WHERE type<>'text')
    ) THEN RAISE(ABORT,'SceneSummary schema failed') END;
END;

CREATE TRIGGER description_summary_schema_update BEFORE UPDATE ON vlm_descriptions
BEGIN
    SELECT CASE WHEN NEW.structured_json IS NOT NULL AND NOT json_valid(NEW.structured_json) THEN RAISE(ABORT,'malformed scene JSON') END;
    SELECT CASE WHEN NEW.output_format='summary' AND NOT (
        COALESCE(json_type(NEW.structured_json)='object',0)
        AND COALESCE(json_type(NEW.structured_json,'$.description')='text' AND length(json_extract(NEW.structured_json,'$.description')) BETWEEN 1 AND 240,0)
        AND COALESCE(json_type(NEW.structured_json,'$.visible_evidence')='array',0)
        AND COALESCE(json_extract(NEW.structured_json,'$.uncertainty') IN ('low','medium','high'),0)
        AND NOT EXISTS(SELECT 1 FROM json_each(NEW.structured_json,'$.visible_evidence') WHERE type<>'text')
    ) THEN RAISE(ABORT,'SceneSummary schema failed') END;
END;

INSERT INTO schema_migrations(version,name,applied_at)
VALUES(16,'minicpm_scene_points',strftime('%Y-%m-%dT%H:%M:%f','now') || '000Z');
COMMIT;
