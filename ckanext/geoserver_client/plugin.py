import logging
from ckan import plugins as p

log = logging.getLogger(__name__)


class GeoServerPlugin(p.SingletonPlugin):
    p.implements(p.IActions)
    p.implements(p.IResourceController, inherit=True)
    p.implements(p.IBlueprint)
    p.implements(p.ITemplateHelpers)
    p.implements(p.IConfigurer)

    def update_config(self, config_):
        p.toolkit.add_template_directory(config_, "templates")

    def get_actions(self):
        from ckanext.geoserver_client.logic import action

        return {
            "geoserver_setup_workspace": action.geoserver_setup_workspace,
            "geoserver_ingest_geojson": action.geoserver_ingest_geojson,
            "geoserver_client_datastore_status": action.geoserver_client_datastore_status,
            "geoserver_client_datastore_submit": action.geoserver_client_datastore_submit,
        }

    def get_blueprint(self):
        from ckanext.geoserver_client.blueprint import get_blueprints

        return get_blueprints()

    def get_helpers(self):
        return {
            "geoserver_client_is_geojson_resource": self._is_geojson_resource,
            "geoserver_client_datastore_status": self._datastore_status_helper,
            "geoserver_client_datastore_status_description": self._datastore_status_description,
        }

    def _datastore_status_helper(self, resource_id):
        """Template helper wrapping geoserver_client_datastore_status, so
        the overridden resource_data.html template can pull OUR job's
        status directly rather than relying on whatever `status` the
        active datapusher/xloader view already put in the page context
        (which would be THEIR status action's result, not ours).
        """
        try:
            return p.toolkit.get_action("geoserver_client_datastore_status")(
                {}, {"id": resource_id}
            )
        except (p.toolkit.ObjectNotFound, p.toolkit.NotAuthorized):
            return {}

    def _datastore_status_description(self, status):
        # Same pattern as ckanext-datapusher's own datapusher_status_description
        # helper (ckanext/datapusher/helpers.py) - kept as a Python helper
        # rather than inline Jinja so the capitalize() fallback is only ever
        # reached once status['status'] is already known to be truthy.
        if status.get("status"):
            captions = {
                "complete": p.toolkit._("Complete"),
                "pending": p.toolkit._("Pending"),
                "working": p.toolkit._("Working"),
                "skipped": p.toolkit._("Skipped"),
                "error": p.toolkit._("Error"),
            }
            return captions.get(status["status"], status["status"].capitalize())
        return p.toolkit._("Not Uploaded Yet")

    def after_resource_create(self, context, resource):
        self._enqueue_geoserver_job(resource)
        self._enqueue_datastore_job(resource)

    def after_resource_update(self, context, resource):
        if context.get("geoserver_updating"):
            # This update came from geoserver_ingest_geojson itself
            # attaching wms_url/wfs_url after a successful publish - not a
            # new upload, so don't re-trigger either job from it.
            return
        self._enqueue_geoserver_job(resource)
        self._enqueue_datastore_job(resource)

    def before_resource_delete(self, context, resource, resources):
        # NOT after_resource_delete: that hook receives the list of
        # resources still remaining on the package after the delete, not
        # the one being deleted - there'd be no way to get this resource's
        # id/url/format from it, since it's already gone from that list.
        # before_resource_delete gives us the resource being deleted itself,
        # while it still exists.
        if self._is_geo_resource(resource):
            from ckanext.geoserver_client.logic.action import delete_geoserver_layer_job

            try:
                p.toolkit.enqueue_job(
                    delete_geoserver_layer_job,
                    [resource["id"]],
                    title=f"Deleting GeoServer layer for {resource['id']}",
                )
            except Exception as e:
                log.error("Failed to enqueue GeoServer delete payload queue: %s", e)

    def _is_geo_resource(self, resource):
        url = resource.get("url", "").lower()
        fmt = resource.get("format", "").lower()
        return (
            fmt in ("geojson", "shp", "shapefile", "shape", "zip", "gpkg", "geopackage")
            or url.endswith(".geojson")
            or url.endswith(".shp")
            or url.endswith(".zip")
            or url.endswith(".gpkg")
        )

    def _is_geojson_resource(self, resource):
        # Narrower than _is_geo_resource: only GeoJSON's geometry is
        # structured data that can be loaded into the DataStore without any
        # column-name guessing (see lib/datastore.py's module docstring).
        # Shapefile/GeoPackage/ZIP still go to GeoServer via
        # _enqueue_geoserver_job, just not to the DataStore.
        url = resource.get("url", "").lower()
        fmt = resource.get("format", "").lower()
        return fmt == "geojson" or url.endswith(".geojson")

    def _enqueue_geoserver_job(self, resource):
        if self._is_geo_resource(resource):
            from ckanext.geoserver_client.logic.action import ingest_geojson_job

            try:
                p.toolkit.enqueue_job(
                    ingest_geojson_job,
                    [resource["id"]],
                    title=f"Uploading isolated GeoJSON to GeoServer {resource['id']}",
                )
            except Exception as e:
                log.error(
                    "Failed to enqueue standalone GeoServer upload payload queue: %s", e
                )

    def _enqueue_datastore_job(self, resource):
        if self._is_geojson_resource(resource):
            from ckanext.geoserver_client.logic.action import datastore_geojson_job

            try:
                p.toolkit.enqueue_job(
                    datastore_geojson_job,
                    [resource["id"]],
                    title=f"Loading GeoJSON {resource['id']} into the datastore",
                )
            except Exception as e:
                log.error("Failed to enqueue datastore load job: %s", e)
