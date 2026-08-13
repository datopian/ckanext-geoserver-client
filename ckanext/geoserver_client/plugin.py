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

    def after_resource_create(self, context, resource):
        self._enqueue_geoserver_job(resource)

    def after_resource_update(self, context, resource):
        if context.get("geoserver_updating"):
            return
        self._enqueue_geoserver_job(resource)

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
