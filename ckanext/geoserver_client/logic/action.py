import json
import os
import re
import logging
import tempfile
import subprocess
import zipfile
import requests
import shutil
from ckan import plugins as p
from ckanext.geoserver_client.lib.geoserver_api import GeoServerAPI

log = logging.getLogger(__name__)

_ILLEGAL_XML_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _geoserver_name(resource_id):
    """Return the GeoServer layer name for a resource.

    XML names (used as WFS element names) cannot start with a digit, but CKAN
    resource IDs are UUIDs which can begin with 0-9.  Prefix those with 'r_'.
    """
    return f"r_{resource_id}"


def _sanitise_geojson(data):
    if isinstance(data, dict):
        return {k: _sanitise_geojson(v) for k, v in data.items()}
    if isinstance(data, list):
        return [_sanitise_geojson(i) for i in data]
    if isinstance(data, str):
        return _ILLEGAL_XML_RE.sub("", data)
    return data


def _base_geometry_types(geojson_data):
    """Return the set of base geometry types (Multi-prefix stripped) across all features."""
    if geojson_data.get("type") == "FeatureCollection":
        features = geojson_data.get("features", [])
    else:
        features = [geojson_data]

    types = set()

    for feat in features:
        geom = feat.get("geometry") if feat.get("type") == "Feature" else feat

        if geom:
            t = geom.get("type", "")
            types.add(t[5:] if t.startswith("Multi") else t)

    return types


def ingest_geojson_job(resource_id):
    context = {
        "ignore_auth": True,
        "user": p.toolkit.get_action("get_site_user")({"ignore_auth": True}, {})[
            "name"
        ],
    }

    try:
        p.toolkit.get_action("geoserver_ingest_geojson")(
            context, {"resource_id": resource_id}
        )
    except Exception as e:
        log.error(f"Background shapefile ingest failed for {resource_id}: {e}")


def _legacy_geoserver_names(resource_id):
    """Datastore names used by older versions of _geoserver_name: the raw
    resource_id, then '_' + resource_id for ids starting with a digit.
    Resources published before the 'r_' prefix may still use these.
    """
    return [resource_id, f"_{resource_id}"]


def delete_geoserver_layer_job(resource_id):
    try:
        geoserver_api = GeoServerAPI()
    except Exception as e:
        log.error(f"Failed to create GeoServer client for {resource_id}: {e}")
        return

    # Delete the current name and any legacy names. delete_layer ignores 404,
    # so names that do not exist cost one request each and nothing else.
    for name in [_geoserver_name(resource_id)] + _legacy_geoserver_names(resource_id):
        try:
            geoserver_api.delete_layer(name)
        except Exception as e:
            log.error(
                f"Failed to cleanly proxy GeoServer layer removal for {name}: {e}"
            )

    # The style is a separate workspace-level catalog object, named after the
    # raw resource_id (not the r_-prefixed layer name; see the style_name
    # assignment in geoserver_ingest_geojson). Deleting the datastore above
    # does not remove it, so it must be cleaned up on its own or it is
    # orphaned in GeoServer forever.
    try:
        geoserver_api.delete_style(f"style_{resource_id}")
    except Exception as e:
        log.error(
            f"Failed to cleanly remove GeoServer style for {resource_id}: {e}"
        )


def _fetch_resource_file(resource, dest_path, api_token=None):
    """
    Fetch a resource file
    """
    resource_id = resource["id"]
    url = resource.get("url", "")

    # Try CKAN local storage path first, if configured and accessible
    try:
        from ckan.plugins import toolkit

        storage_path = toolkit.config.get("ckan.storage_path", "/var/lib/ckan")
        local_path = os.path.join(
            storage_path,
            "resources",
            resource_id[0:3],
            resource_id[3:6],
            resource_id[6:],
        )

        if os.path.isfile(local_path):
            log.debug(f"Reading {resource_id} directly from disk: {local_path}")
            shutil.copy2(local_path, dest_path)
            return
        else:
            log.debug(f"Local storage path not found for {resource_id}: {local_path}")
    except Exception as e:
        log.debug(f"Local storage path check failed for {resource_id}: {e}")

    # Try boto3 (S3 / MinIO) if not in local storage, and if boto3 is available
    try:
        import boto3
        from botocore.config import Config
        from ckan.plugins import toolkit

        bucket = toolkit.config.get("ckanext.s3filestore.aws_bucket_name")
        key_id = toolkit.config.get("ckanext.s3filestore.aws_access_key_id")
        secret = toolkit.config.get("ckanext.s3filestore.aws_secret_access_key")
        endpoint = toolkit.config.get(
            "ckanext.s3filestore.host_name"
        ) or toolkit.config.get("ckanext.s3filestore.aws_host_name")
        region = toolkit.config.get("ckanext.s3filestore.region_name", "us-east-1")
        # ckanext-s3filestore's S3ResourceUploader always keys resources as
        # <aws_storage_path>/resources/<resource_id>/<filename> (storage_path
        # defaults to '' when unset, NOT 'resources' - that default text was
        # only ever a coincidental match for the no-config case, since it
        # never appended a further 'resources' segment on top of it).
        storage_path = toolkit.config.get(
            "ckanext.s3filestore.aws_storage_path", ""
        ).strip("/")

        if bucket and key_id and secret and endpoint:
            s3 = boto3.client(
                "s3",
                endpoint_url=endpoint,
                aws_access_key_id=key_id,
                aws_secret_access_key=secret,
                region_name=region,
                config=Config(signature_version="s3v4"),
            )

            filename = url.rstrip("/").split("/")[-1] if url else ""

            # The real key ckanext-s3filestore actually uses - tried first.
            s3filestore_key = (
                "/".join(filter(None, [storage_path, "resources", resource_id, filename]))
                if filename
                else None
            )
            # Older/alternate layouts, kept as fallbacks in case a resource
            # was stored under a different convention.
            nested_key = f"{storage_path}/{resource_id[0:3]}/{resource_id[3:6]}/{resource_id[6:]}"
            flat_key = f"{storage_path}/{resource_id}"
            subdir_key = (
                f"{storage_path}/{resource_id}/{filename}" if filename else None
            )

            keys_to_try = [k for k in (s3filestore_key, nested_key, flat_key, subdir_key) if k]

            for object_key in keys_to_try:
                try:
                    log.debug(f"Trying s3://{bucket}/{object_key}")
                    s3.download_file(bucket, object_key, dest_path)
                    return
                except Exception as e:
                    log.debug(f"S3 key {object_key} failed: {e}")
    except ImportError:
        pass
    except Exception as e:
        log.debug(f"S3 setup failed for {resource_id}: {e}")

    # Fallback to HTTP fetch if all else fails
    log.debug(f"Falling back to HTTP fetch for {resource_id}: {url}")
    headers = {}

    if api_token:
        headers["Authorization"] = f"Bearer {api_token}"

    resp = requests.get(url, stream=True, timeout=30, headers=headers)
    resp.raise_for_status()

    with open(dest_path, "wb") as f:
        for chunk in resp.iter_content(chunk_size=8192):
            f.write(chunk)

    # Final check: ensure the file actually exists and is not empty
    if not os.path.exists(dest_path) or os.path.getsize(dest_path) == 0:
        raise Exception(f"Failed to fetch content for resource {resource_id}")


def _is_sld(resource):
    return (resource.get("format") or "").lower() == "sld"


def _dataset_sld(dataset):
    """The SLD resource used to style every geo layer in the dataset: the
    first active resource with format SLD, or None.
    """
    return next((r for r in dataset.get("resources", []) if _is_sld(r)), None)


def _sync_layer_style(
    geoserver_api, resource_id, layer_name, sld_res, base_dir, api_token=None
):
    """Make the layer's style match the dataset's SLD.

    With an SLD: upload it as style_<resource_id> and set it as the layer's
    default style. Without one: delete style_<resource_id>. GeoServer then
    resets the layer to its built-in default style (see delete_style).
    Errors are logged, not raised, so a style problem never fails ingest.
    """
    style_name = f"style_{resource_id}"

    if not sld_res:
        try:
            geoserver_api.delete_style(style_name)
        except Exception as e:
            log.error(f"Failed to remove GeoServer style {style_name}: {e}")
        return

    try:
        sld_path = os.path.join(base_dir, f"style_{sld_res['id']}.sld")
        _fetch_resource_file(sld_res, sld_path, api_token=api_token)

        with open(sld_path, "rb") as f:
            raw = f.read()
        try:
            sld_body = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            sld_body = raw.decode("latin-1")

        layer = f"{geoserver_api.workspace}:{layer_name}"
        sld_body = re.sub(
            r"(<NamedLayer>\s*<Name>)[^<]*(</Name>)",
            lambda m: f"{m.group(1)}{layer}{m.group(2)}",
            sld_body,
            count=1,
            flags=re.IGNORECASE,
        )

        if geoserver_api.upload_style(style_name, sld_body):
            geoserver_api.set_layer_style(layer_name, style_name)
    except Exception as e:
        log.error(
            f"Failed to cleanly apply SLD style {sld_res.get('id')} to {resource_id}: {e}"
        )


def refresh_dataset_styles_job(package_id):
    """Re-sync the style of every published geo layer in a dataset after an
    SLD resource was created, changed or deleted. Only styles change; the
    data is not re-uploaded.
    """
    context = {
        "ignore_auth": True,
        "user": p.toolkit.get_action("get_site_user")({"ignore_auth": True}, {})[
            "name"
        ],
    }
    try:
        dataset = p.toolkit.get_action("package_show")(context, {"id": package_id})
    except p.toolkit.ObjectNotFound:
        return

    geoserver_api = GeoServerAPI()
    sld_res = _dataset_sld(dataset)
    base_dir = tempfile.mkdtemp()
    try:
        for res in dataset.get("resources", []):
            # geoserver_layer is "<workspace>:<layer name>", set on ingest.
            # Resources without it were never published, so skip them.
            layer = res.get("geoserver_layer") or ""
            if ":" not in layer:
                continue
            _sync_layer_style(
                geoserver_api, res["id"], layer.split(":", 1)[1], sld_res, base_dir
            )
    finally:
        shutil.rmtree(base_dir, ignore_errors=True)


def geoserver_ingest_geojson(context, data_dict):
    """
    Ingest a GeoJSON, Shapefile (ZIP), or GeoPackage resource, convert to
    Shapefile if needed, and publish to GeoServer with optional SLD styling.
    """
    p.toolkit.check_access("package_update", context, data_dict)
    resource_id = p.toolkit.get_or_bust(data_dict, "resource_id")
    resource = p.toolkit.get_action("resource_show")(context, {"id": resource_id})
    url = resource.get("url") or ""
    fmt = resource.get("format", "").lower()
    url_lower = url.lower()

    is_geojson = fmt == "geojson" or url_lower.endswith(".geojson")
    is_shapefile = fmt in ("shp", "shapefile", "shape") or url_lower.endswith(".shp")
    is_zip = fmt == "zip" or url_lower.endswith(".zip")
    is_gpkg = fmt in ("gpkg", "geopackage") or url_lower.endswith(".gpkg")

    if not url or not (is_geojson or is_shapefile or is_zip or is_gpkg):
        return {"status": "skipped", "reason": "Not a supported geo format"}

    base_dir = tempfile.mkdtemp()
    geoserver_name = _geoserver_name(resource_id)
    shp_path = os.path.join(base_dir, f"{geoserver_name}.shp")
    zip_path = os.path.join(base_dir, f"{resource_id}.zip")
    api_token = context.get("api_token")

    try:
        if is_geojson:
            geojson_path = os.path.join(base_dir, f"{resource_id}.geojson")
            _fetch_resource_file(resource, geojson_path, api_token=api_token)

            # Validate the file is actually GeoJSON before handing to ogr2ogr
            try:
                with open(geojson_path, "r", encoding="utf-8-sig") as f:
                    geojson_data = json.load(f)
                valid_types = {
                    "FeatureCollection",
                    "Feature",
                    "Point",
                    "MultiPoint",
                    "LineString",
                    "MultiLineString",
                    "Polygon",
                    "MultiPolygon",
                    "GeometryCollection",
                }
                if geojson_data.get("type") not in valid_types:
                    log.warning(
                        f"Resource {resource_id} has format=geojson but file is not valid GeoJSON (type={geojson_data.get('type')!r}), skipping"
                    )
                    return {
                        "status": "skipped",
                        "reason": "File content is not valid GeoJSON",
                    }
            except (json.JSONDecodeError, UnicodeDecodeError) as e:
                log.warning(
                    f"Resource {resource_id} has format=geojson but file could not be parsed as JSON: {e}, skipping"
                )
                return {
                    "status": "skipped",
                    "reason": "File content is not valid GeoJSON",
                }

            # Skip files with geometry types that shapefiles can't represent
            base_types = _base_geometry_types(geojson_data)

            if "GeometryCollection" in base_types or len(base_types) > 1:
                log.warning(
                    f"Resource {resource_id} has unsupported geometry mix {base_types}, skipping"
                )
                return {
                    "status": "skipped",
                    "reason": f"Unsupported geometry types: {base_types}",
                }

            # Strip XML-illegal control characters from attribute values before
            # handing to ogr2ogr — GeoServer's GML output will reject them.
            geojson_data = _sanitise_geojson(geojson_data)

            with open(geojson_path, "w", encoding="utf-8") as f:
                json.dump(geojson_data, f)

            cmd = [
                "ogr2ogr",
                "-f",
                "ESRI Shapefile",
                shp_path,
                geojson_path,
                "-nln",
                geoserver_name,
                "-nlt",
                "PROMOTE_TO_MULTI",
                "-lco",
                "ENCODING=UTF-8",
                "-overwrite",
            ]
            proc = subprocess.run(cmd, capture_output=True, text=True, errors="replace")

            if proc.returncode != 0:
                log.error(f"ogr2ogr conversion to shapefile failed: {proc.stderr}")
                raise Exception(f"ogr2ogr conversion failed: {proc.stderr}")

        elif is_shapefile or is_zip:
            raw_path = os.path.join(base_dir, "input")
            _fetch_resource_file(resource, raw_path, api_token=api_token)

            input_shp = None

            try:
                extract_dir = os.path.join(base_dir, "extracted")
                os.makedirs(extract_dir)

                with zipfile.ZipFile(raw_path, "r") as zf:
                    zf.extractall(extract_dir)

                shp_files = [
                    os.path.join(extract_dir, f)
                    for f in os.listdir(extract_dir)
                    if f.lower().endswith(".shp")
                ]

                if not shp_files:
                    return {
                        "status": "skipped",
                        "reason": "ZIP does not contain a shapefile",
                    }
                input_shp = shp_files[0]
            except zipfile.BadZipFile:
                if not is_shapefile:
                    return {
                        "status": "skipped",
                        "reason": "ZIP file is corrupt or invalid",
                    }
                # format=SHP but not a zip — try using the file directly
                input_shp = raw_path

            cmd = [
                "ogr2ogr",
                "-f",
                "ESRI Shapefile",
                shp_path,
                input_shp,
                "-nln",
                geoserver_name,
                "-nlt",
                "PROMOTE_TO_MULTI",
                "-lco",
                "ENCODING=UTF-8",
                "-overwrite",
            ]
            proc = subprocess.run(cmd, capture_output=True, text=True, errors="replace")

            if proc.returncode != 0:
                log.error(f"ogr2ogr shapefile normalisation failed: {proc.stderr}")
                raise Exception(
                    f"ogr2ogr shapefile normalisation failed: {proc.stderr}"
                )

        else:  # is_gpkg
            gpkg_path = os.path.join(base_dir, f"{resource_id}.gpkg")
            _fetch_resource_file(resource, gpkg_path, api_token=api_token)

            cmd = [
                "ogr2ogr",
                "-f",
                "ESRI Shapefile",
                shp_path,
                gpkg_path,
                "-nln",
                geoserver_name,
                "-nlt",
                "PROMOTE_TO_MULTI",
                "-lco",
                "ENCODING=UTF-8",
                "-overwrite",
            ]
            proc = subprocess.run(cmd, capture_output=True, text=True, errors="replace")

            if proc.returncode != 0:
                log.error(f"ogr2ogr GeoPackage conversion failed: {proc.stderr}")
                raise Exception(f"ogr2ogr GeoPackage conversion failed: {proc.stderr}")

        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zipf:
            for ext in [".shp", ".shx", ".dbf", ".prj", ".cpg"]:
                f = os.path.join(base_dir, f"{geoserver_name}{ext}")
                if os.path.exists(f):
                    zipf.write(f, os.path.basename(f))

        # Push the Shapefile to GeoServer, link WMS/WFS endpoints, apply SLD
        geoserver_api = GeoServerAPI()
        geoserver_api.upload_shapefile(geoserver_name, zip_path)

        # Set a human-readable title on the layer from the CKAN resource name
        layer_title = resource.get("name") or resource_id
        geoserver_api.update_layer_title(geoserver_name, layer_title)

        base_url = p.toolkit.config.get(
            "ckanext.geoserver_client.public_url", "http://localhost:8080/geoserver"
        )
        workspace = geoserver_api.workspace
        layer = f"{workspace}:{geoserver_name}"

        # Apply the dataset's SLD, or remove a stale style if it has none
        dataset = p.toolkit.get_action("package_show")(
            context, {"id": resource.get("package_id")}
        )
        _sync_layer_style(
            geoserver_api,
            resource_id,
            geoserver_name,
            _dataset_sld(dataset),
            base_dir,
            api_token=api_token,
        )

        bbox = geoserver_api.get_bounding_box(geoserver_name)
        bbox_suffix = f"&bbox={bbox}" if bbox else ""

        # Virtual OGC service URLs — scoped to this layer only so QGIS
        # GetCapabilities returns exactly one layer instead of the whole workspace.
        # layers/typeName let the portal frontend identify the layer without
        # parsing the URL path; bbox drives the initial map zoom.
        resource["wms_url"] = (
            f"{base_url.rstrip('/')}/{workspace}/{geoserver_name}/wms"
            f"?service=WMS&request=GetCapabilities&layers={layer}{bbox_suffix}"
        )
        resource["wfs_url"] = (
            f"{base_url.rstrip('/')}/{workspace}/{geoserver_name}/wfs"
            f"?service=WFS&request=GetCapabilities&typeName={layer}{bbox_suffix}"
        )
        resource["geoserver_layer"] = layer

        context["geoserver_updating"] = True
        p.toolkit.get_action("resource_update")(context, resource)

        return {"status": "success", "resource_id": resource_id}

    except Exception as e:
        log.error(f"Failed to ingest geo resource {resource_id}: {e}")
        raise p.toolkit.ValidationError({"ogr2ogr_shapefile_error": str(e)})
    finally:
        shutil.rmtree(base_dir, ignore_errors=True)


@p.toolkit.side_effect_free
def geoserver_setup_workspace(context, data_dict):
    p.toolkit.check_access("sysadmin", context, data_dict)
    geoserver_api = GeoServerAPI()

    try:
        ws_status = geoserver_api.ensure_workspace()
        return {
            "success": True,
            "message": f"Workspace is totally healthy ({ws_status})",
        }
    except Exception as e:
        raise p.toolkit.ValidationError({"workspace_error": str(e)})


# Datastore names are r_<uuid> (see _geoserver_name), or <uuid> / _<uuid> for
# resources published by older versions (see _legacy_geoserver_names). Style
# names are always style_<uuid> - the raw resource_id, with no 'r_' prefix
# (see the style_name assignment above). Anything not matching these patterns
# was not created by this extension and is left alone.
_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
_DATASTORE_NAME_RE = re.compile(rf"^(?:r_|_)?(?P<id>{_UUID})$", re.IGNORECASE)
_STYLE_NAME_RE = re.compile(rf"^style_(?P<id>{_UUID})$", re.IGNORECASE)


def _resource_is_gone(resource_id):
    """True if the resource no longer exists or is deleted in CKAN, or its
    dataset is deleted. package_delete leaves the resources of a deleted
    dataset in the 'active' state, so the dataset state must be checked too.
    Draft resources/datasets are not treated as gone.
    """
    import ckan.model as model

    resource = model.Resource.get(resource_id)
    if resource is None or resource.state == "deleted":
        return True
    package = resource.package
    return package is None or package.state == "deleted"


def _datastore_is_orphaned(name, resource_id, workspace):
    """True if the resource is gone, or if the resource now points at a
    different GeoServer layer (e.g. a legacy-named datastore left behind
    after the resource was republished under the r_ name).
    """
    if _resource_is_gone(resource_id):
        return True

    import ckan.model as model

    current_layer = model.Resource.get(resource_id).extras.get("geoserver_layer")
    # No layer recorded: we cannot tell which datastore is live, so keep it.
    return bool(current_layer) and current_layer != f"{workspace}:{name}"


def _style_is_orphaned(resource_id):
    """True if the resource is gone, or its dataset no longer has an active
    SLD resource (the SLD was deleted, or its format changed).
    """
    if _resource_is_gone(resource_id):
        return True

    import ckan.model as model

    package = model.Resource.get(resource_id).package
    return not any(
        r.state == "active" and (r.format or "").lower() == "sld"
        for r in package.resources_all
    )


@p.toolkit.side_effect_free
def geoserver_find_orphans(context, data_dict):
    """List GeoServer datastores/styles whose CKAN resource no longer exists
    or is deleted (or whose dataset is deleted), styles whose dataset no
    longer has an SLD, plus legacy-named datastores that the resource no
    longer points at. Read-only - does not delete
    anything.
    """
    p.toolkit.check_access("sysadmin", context, data_dict)
    geoserver_api = GeoServerAPI()
    workspace = geoserver_api.workspace

    orphaned_datastores = [
        name
        for name in geoserver_api.list_datastores()
        if (match := _DATASTORE_NAME_RE.match(name))
        and _datastore_is_orphaned(name, match.group("id"), workspace)
    ]
    orphaned_styles = [
        name
        for name in geoserver_api.list_styles()
        if (match := _STYLE_NAME_RE.match(name))
        and _style_is_orphaned(match.group("id"))
    ]

    return {
        "orphaned_datastores": orphaned_datastores,
        "orphaned_styles": orphaned_styles,
    }


def geoserver_delete_orphans(context, data_dict):
    """Delete the GeoServer datastores/styles named in data_dict (as returned
    by geoserver_find_orphans). Never guesses - only deletes exact names
    handed to it.
    """
    p.toolkit.check_access("sysadmin", context, data_dict)
    geoserver_api = GeoServerAPI()

    deleted_datastores = []
    failed_datastores = []
    for name in data_dict.get("orphaned_datastores", []):
        try:
            geoserver_api.delete_layer(name)
            deleted_datastores.append(name)
        except Exception as e:
            log.error(f"Failed to delete orphaned datastore {name}: {e}")
            failed_datastores.append(name)

    deleted_styles = []
    failed_styles = []
    for name in data_dict.get("orphaned_styles", []):
        try:
            geoserver_api.delete_style(name)
            deleted_styles.append(name)
        except Exception as e:
            log.error(f"Failed to delete orphaned style {name}: {e}")
            failed_styles.append(name)

    return {
        "deleted_datastores": deleted_datastores,
        "failed_datastores": failed_datastores,
        "deleted_styles": deleted_styles,
        "failed_styles": failed_styles,
    }
