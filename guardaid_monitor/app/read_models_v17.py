"""Current workflow tokens; historical migration verification retains v15 tokens."""
from . import read_models_v15 as previous

PROJECTIONS = dict(previous.PROJECTIONS,
    workflow_state='SELECT id AS alert_id,workflow_status,handler_user_id,active_phase_id,broadcast_role_snapshot_json AS broadcast_role_ids_json,workflow_revision AS revision,workflow_updated_at AS updated_at FROM alerts')


def read_sql(sql):
    return previous.read_sql(sql, projections=PROJECTIONS)
