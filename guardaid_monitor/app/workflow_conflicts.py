"""Stable conflict codes with messages intended for incident responders."""


class WorkflowConflictError(ValueError):
    def __init__(self, message, code, workflow):
        super().__init__(message)
        self.code = code
        self.workflow_status = workflow['workflow_status']
        self.workflow_revision = int(workflow['revision'])


def stale_workflow_error(workflow):
    message = {
        'resolved': 'This incident has already been resolved.',
        'dismissed': 'This alert has already been dismissed as a false alarm.',
        'confirmed_awaiting_handler': 'This incident has already been confirmed and is awaiting a handler.',
        'in_progress': 'This incident is now being handled.',
    }.get(workflow['workflow_status'], 'The incident response has changed.')
    return WorkflowConflictError(
        message + ' Review the latest details before responding.',
        'workflow_changed', workflow,
    )
