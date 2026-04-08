import logging
from ckan import plugins as p

log = logging.getLogger(__name__)


class GeoServerPlugin(p.SingletonPlugin):
    p.implements(p.IActions)
    p.implements(p.IResourceController, inherit=True)

    def get_actions(self):
        from ckanext.geoserver_client.logic import action

        return {
            "geoserver_setup_workspace": action.geoserver_setup_workspace,
            "geoserver_ingest_geojson": action.geoserver_ingest_geojson,
        }

    def after_create(self, context, resource):
        self._enqueue_geoserver_job(resource)

    def after_update(self, context, resource):
        self._enqueue_geoserver_job(resource)

    def after_delete(self, context, resource):
        url = resource.get("url", "").lower()
        fmt = resource.get("format", "").lower()

        if fmt == "geojson" or url.endswith(".geojson"):
            from ckanext.geoserver_client.logic.action import delete_geoserver_layer_job

            try:
                p.toolkit.enqueue_job(
                    delete_geoserver_layer_job,
                    [resource["id"]],
                    title=f"Deleting GeoServer layer for {resource['id']}",
                )
            except Exception as e:
                log.error("Failed to enqueue GeoServer delete payload queue: %s", e)

    def _enqueue_geoserver_job(self, resource):
        url = resource.get("url", "").lower()
        fmt = resource.get("format", "").lower()

        if fmt == "geojson" or url.endswith(".geojson"):
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
