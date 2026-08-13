# encoding: utf-8
"""Job-status tracking for the GeoJSON-to-DataStore pipeline, using CKAN
core's own task_status table - the same mechanism ckanext-datapusher/
ckanext-xloader use for their own upload jobs (ckan/ckanext/datapusher/
logic/action.py's datapusher_status/datapusher_submit).

Unlike DataPusher/xloader, there's no separate microservice to poll for
logs - everything runs in-process, so this stores logs directly in
task_status's own `value` JSON blob and reads them back the same way,
returning the exact shape ckanext-datapusher/xloader's resource_data.html
template already expects (status/last_updated/task_info.logs/error) so
that template can be reused almost as-is.
"""

import datetime
import json
import logging

from ckan.plugins import toolkit

log = logging.getLogger(__name__)

TASK_TYPE = "geoserver_client_datastore"
TASK_KEY = "geoserver_client_datastore"

DEFAULT_CONTEXT = {"ignore_auth": True}


def _get_task(resource_id):
    try:
        return toolkit.get_action("task_status_show")(
            dict(DEFAULT_CONTEXT),
            {"entity_id": resource_id, "task_type": TASK_TYPE, "key": TASK_KEY},
        )
    except toolkit.ObjectNotFound:
        return None


def start(resource_id):
    """Mark a fresh submission as pending, clearing any previous logs."""
    _save(resource_id, state="pending", logs=[], error=None)


def log_step(resource_id, message, level="INFO"):
    """Append one log line (matching DataPusher's log shape: level/message/
    timestamp) and mark the job as working.
    """
    task = _get_task(resource_id) or {}
    value = json.loads(task.get("value") or "{}")
    logs = value.get("logs") or []
    logs.append(
        {
            "level": level,
            "message": message,
            "timestamp": datetime.datetime.utcnow().isoformat(),
        }
    )
    _save(resource_id, state="working", logs=logs, error=None)


def complete(resource_id):
    task = _get_task(resource_id) or {}
    value = json.loads(task.get("value") or "{}")
    _save(resource_id, state="complete", logs=value.get("logs") or [], error=None)


def skip(resource_id, message):
    """A well-understood no-op outcome (e.g. re-running the pipeline on a
    GeoJSON with no geometry) - distinct from an error, since nothing
    actually went wrong.
    """
    task = _get_task(resource_id) or {}
    value = json.loads(task.get("value") or "{}")
    logs = value.get("logs") or []
    logs.append(
        {
            "level": "INFO",
            "message": message,
            "timestamp": datetime.datetime.utcnow().isoformat(),
        }
    )
    _save(resource_id, state="skipped", logs=logs, error=None)


def error(resource_id, message):
    task = _get_task(resource_id) or {}
    value = json.loads(task.get("value") or "{}")
    logs = value.get("logs") or []
    logs.append(
        {
            "level": "ERROR",
            "message": message,
            "timestamp": datetime.datetime.utcnow().isoformat(),
        }
    )
    _save(resource_id, state="error", logs=logs, error={"message": message})


def _save(resource_id, state, logs, error):
    existing = _get_task(resource_id)
    task = {
        "entity_id": resource_id,
        "entity_type": "resource",
        "task_type": TASK_TYPE,
        "key": TASK_KEY,
        "state": state,
        "last_updated": datetime.datetime.utcnow().isoformat(),
        "value": json.dumps({"logs": logs}),
        "error": json.dumps(error),
    }
    if existing:
        task["id"] = existing["id"]
    toolkit.get_action("task_status_update")(dict(DEFAULT_CONTEXT), task)


def get_status(resource_id):
    """Same return shape as ckanext-datapusher's datapusher_status action,
    so the (overridden) resource_data.html template can be reused as-is:
    status/last_updated/task_info.logs (with real datetime objects, like
    DataPusher hydrates from its remote job payload)/error.
    """
    task = _get_task(resource_id)
    if not task:
        return {
            "status": None,
            "last_updated": None,
            "task_info": {"logs": []},
            "error": None,
        }

    value = json.loads(task.get("value") or "{}")
    logs = value.get("logs") or []
    for entry in logs:
        ts = entry.get("timestamp")
        if isinstance(ts, str):
            entry["timestamp"] = datetime.datetime.fromisoformat(ts)

    error_value = task.get("error")
    error_dict = json.loads(error_value) if error_value else None

    return {
        "status": task.get("state"),
        "last_updated": task.get("last_updated"),
        "task_info": {"logs": logs},
        "error": error_dict,
    }
