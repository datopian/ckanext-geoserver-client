import logging
from ckan import plugins as p

log = logging.getLogger(__name__)


def _is_sld(resource):
    return (resource.get("format") or "").lower() == "sld"


class GeoServerPlugin(p.SingletonPlugin):
    p.implements(p.IActions)
    p.implements(p.IResourceController, inherit=True)
    p.implements(p.IPackageController, inherit=True)

    def get_actions(self):
        from ckanext.geoserver_client.logic import action

        return {
            "geoserver_setup_workspace": action.geoserver_setup_workspace,
            "geoserver_ingest_geojson": action.geoserver_ingest_geojson,
            "geoserver_find_orphans": action.geoserver_find_orphans,
            "geoserver_delete_orphans": action.geoserver_delete_orphans,
        }

    def after_resource_create(self, context, resource):
        self._enqueue_geoserver_job(resource)
        if _is_sld(resource):
            self._enqueue_style_refresh_job(resource.get("package_id"))

    def before_resource_update(self, context, current, resource):
        # Record whether this resource was an SLD before the update, so a
        # format change away from SLD also refreshes the dataset's styles.
        context["geoserver_was_sld"] = _is_sld(current)

    def after_resource_update(self, context, resource):
        if context.get("geoserver_updating"):
            return
        self._enqueue_geoserver_job(resource)
        if _is_sld(resource) or context.pop("geoserver_was_sld", False):
            self._enqueue_style_refresh_job(resource.get("package_id"))

    def before_resource_delete(self, context, resource, resources):
        # NOT after_resource_delete: that hook receives the list of
        # resources still remaining on the package after the delete, not
        # the one being deleted.
        # before_resource_delete runs while the resource still exists, but
        # `resource` is only the resource_delete data_dict (usually just
        # {"id": ...}), so look up the full resource in `resources` (the
        # package's resources before the delete) to get its url/format.
        full_resource = next(
            (r for r in resources if r.get("id") == resource.get("id")), resource
        )
        self._enqueue_delete_job(full_resource)
        if _is_sld(full_resource):
            # Refresh styles in after_resource_delete, not here: a worker
            # could run the job before the delete is committed and still see
            # this SLD. The context is shared between the two hooks.
            context["geoserver_sld_deleted_from"] = full_resource.get("package_id")

    def after_resource_delete(self, context, resources):
        package_id = context.pop("geoserver_sld_deleted_from", None)
        if package_id:
            self._enqueue_style_refresh_job(package_id)

    def after_dataset_delete(self, context, data_dict):
        # package_delete does not call resource_delete, and leaves the
        # resources of the deleted dataset in the 'active' state, so remove
        # their GeoServer layers here. This runs before the dataset state is
        # set to 'deleted'.
        import ckan.model as model

        package = model.Package.get(data_dict.get("id"))
        if package is None:
            return
        for res in package.resources:
            self._enqueue_delete_job({"id": res.id, "url": res.url, "format": res.format})

    def _enqueue_style_refresh_job(self, package_id):
        if not package_id:
            return
        from ckanext.geoserver_client.logic.action import refresh_dataset_styles_job

        try:
            p.toolkit.enqueue_job(
                refresh_dataset_styles_job,
                [package_id],
                title=f"Refreshing GeoServer styles for dataset {package_id}",
            )
        except Exception as e:
            log.error("Failed to enqueue GeoServer style refresh job: %s", e)

    def _enqueue_delete_job(self, resource):
        if not self._is_geo_resource(resource):
            return
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
        url = (resource.get("url") or "").lower()
        fmt = (resource.get("format") or "").lower()
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
