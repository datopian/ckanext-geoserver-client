# encoding: utf-8
"""Flask routes backing the DataStore tab's controls for GeoJSON resources.

"Upload to DataStore" for a GeoJSON resource needs to (re-)trigger our own
pipeline (lib/datastore.py, via logic/action.py's
geoserver_client_datastore_submit) - DataPusher's/xloader's own submit
actions don't understand GeoJSON at all, so posting to their routes would
either no-op or misbehave. "Delete from DataStore" is handled here too
(even though the generic datastore_delete-backed routes DataPusher/xloader
already expose would work just as well) purely so the overridden
resource_data.html template (see templates/datapusher and templates/xloader)
doesn't need to know or guess whichever of the two plugins is actually
active in order to build its form action.

Redirects use a plain, hardcoded URL path rather than Flask's url_for/
endpoint-name lookup, since which blueprint (datapusher vs xloader) actually
owns the canonical `/dataset/<id>/resource_data/<resource_id>` route depends
on which is active in CKAN__PLUGINS - the path itself is the one thing both
are guaranteed to expose identically (it's the URL CKAN core's own tab link
points at either way).
"""

import logging

from flask import Blueprint, redirect

import ckan.logic as logic
import ckan.plugins.toolkit as toolkit
from ckan.common import _

log = logging.getLogger(__name__)

geoserver_client = Blueprint("geoserver_client", __name__)


def get_blueprints():
    return [geoserver_client]


def _resource_data_url(id, resource_id):
    return f"/dataset/{id}/resource_data/{resource_id}"


def datastore_submit(id, resource_id):
    try:
        toolkit.get_action("geoserver_client_datastore_submit")(
            {}, {"resource_id": resource_id}
        )
    except logic.ValidationError:
        pass
    except toolkit.NotAuthorized:
        return toolkit.abort(
            403,
            _("Not authorized to resubmit resource {resource_id}").format(
                resource_id=resource_id
            ),
        )

    return redirect(_resource_data_url(id, resource_id))


def datastore_delete(id, resource_id):
    context = {"user": toolkit.current_user.name}
    try:
        toolkit.get_action("datastore_delete")(
            context, {"resource_id": resource_id, "force": True}
        )
    except toolkit.NotAuthorized:
        return toolkit.abort(
            403,
            _("Unauthorized to delete resource {resource_id}").format(
                resource_id=resource_id
            ),
        )
    except toolkit.ObjectNotFound:
        return toolkit.abort(
            404,
            _("Resource not found in datastore {resource_id}").format(
                resource_id=resource_id
            ),
        )

    toolkit.h.flash_notice(
        _("DataStore deleted for resource {resource_id}").format(
            resource_id=resource_id
        )
    )
    return redirect(_resource_data_url(id, resource_id))


geoserver_client.add_url_rule(
    "/dataset/<id>/geojson-datastore/<resource_id>/resubmit",
    view_func=datastore_submit,
    methods=("POST",),
)
geoserver_client.add_url_rule(
    "/dataset/<id>/geojson-datastore/<resource_id>/delete",
    view_func=datastore_delete,
    methods=("POST",),
)
