import os
import logging
import tempfile
import subprocess
import zipfile
import requests
import shutil
import urllib.parse
from ckan import plugins as p
from ckanext.geoserver_client.lib.geoserver_api import GeoServerAPI

log = logging.getLogger(__name__)


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


def delete_geoserver_layer_job(resource_id):
    try:
        from ckanext.geoserver_client.lib.geoserver_api import GeoServerAPI

        geoserver_api = GeoServerAPI()
        geoserver_api.delete_layer(resource_id)
    except Exception as e:
        log.error(
            f"Failed to cleanly proxy GeoServer layer removal for {resource_id}: {e}"
        )


@p.toolkit.side_effect_free
def geoserver_ingest_geojson(context, data_dict):
    """
    Ingest a GeoJSON resource, convert to Shapefile, and publish to GeoServer with optional SLD styling if attached to the parent dataset.
    """
    p.toolkit.check_access("package_update", context, data_dict)
    resource_id = p.toolkit.get_or_bust(data_dict, "resource_id")
    resource = p.toolkit.get_action("resource_show")(context, {"id": resource_id})
    url = resource.get("url")
    fmt = resource.get("format", "").lower()

    if not url or (fmt != "geojson" and not url.lower().endswith(".geojson")):
        return {"status": "skipped", "reason": "Not a GeoJSON file"}

    base_dir = tempfile.mkdtemp()
    geojson_path = os.path.join(base_dir, f"{resource_id}.geojson")
    shp_path = os.path.join(base_dir, f"{resource_id}.shp")
    zip_path = os.path.join(base_dir, f"{resource_id}.zip")

    try:
        headers = {}

        if context.get("user"):
            try:
                user_obj = p.toolkit.get_action("user_show")(
                    context, {"id": context["user"]}
                )

                if user_obj and "apikey" in user_obj:
                    headers["Authorization"] = user_obj["apikey"]
            except Exception:
                pass

        resp = requests.get(url, stream=True, timeout=15, headers=headers)
        resp.raise_for_status()

        with open(geojson_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=8192):
                f.write(chunk)

        cmd = [
            "ogr2ogr",
            "-f",
            "ESRI Shapefile",
            shp_path,
            geojson_path,
            "-nln",
            resource_id,
            "-nlt",
            "PROMOTE_TO_MULTI",
            "-overwrite",
        ]

        proc = subprocess.run(cmd, capture_output=True, text=True, errors="replace")

        if proc.returncode != 0:
            log.error(f"ogr2ogr conversion to shapefile failed: {proc.stderr}")
            raise Exception(f"ogr2ogr conversion failed: {proc.stderr}")

        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zipf:
            for ext in [".shp", ".shx", ".dbf", ".prj", ".cpg"]:
                f = os.path.join(base_dir, f"{resource_id}{ext}")
                if os.path.exists(f):
                    zipf.write(f, os.path.basename(f))

        # Push the Shapefile to the GeoServer, link WMS/WFS endpoints, and apply any attached SLD styling
        geoserver_api = GeoServerAPI()
        geoserver_api.upload_shapefile(resource_id, zip_path)

        # Link GeoServer endpoints dynamically to the frontend WMS map configurations
        base_url = p.toolkit.config.get(
            "ckanext.geoserver_client.public_url", "http://localhost:8080/geoserver"
        )
        workspace = geoserver_api.workspace
        layer = f"{workspace}:{resource_id}"

        # Check for SLD resources attached to the parent dataset and apply if found
        dataset = p.toolkit.get_action("package_show")(
            context, {"id": resource.get("package_id")}
        )
        sld_res = next(
            (
                r
                for r in dataset.get("resources", [])
                if r.get("format", "").lower() == "sld"
            ),
            None,
        )
        if sld_res and sld_res.get("url"):
            try:
                import re

                sld_resp = requests.get(sld_res["url"], timeout=10)
                sld_resp.raise_for_status()

                sld_body = re.sub(
                    r"(<NamedLayer>\s*<Name>)[^<]*(</Name>)",
                    lambda m: f"{m.group(1)}{layer}{m.group(2)}",
                    sld_resp.text,
                    count=1,
                    flags=re.IGNORECASE,
                )

                style_name = f"style_{resource_id}"

                if geoserver_api.upload_style(style_name, sld_body):
                    geoserver_api.set_layer_style(resource_id, style_name)
            except Exception as e:
                log.error(
                    f"Failed to cleanly apply SLD style {sld_res.get('id')} to {resource_id}: {e}"
                )

        bbox = geoserver_api.get_bounding_box(resource_id)
        bbox_param = f"&bbox={urllib.parse.quote(bbox)}" if bbox else ""

        safe_layer = urllib.parse.quote(layer)
        resource["wms_url"] = (
            f"{base_url.rstrip('/')}/{workspace}/wms?service=WMS&version=1.3.0&request=GetCapabilities&layers={safe_layer}{bbox_param}"
        )
        resource["wfs_url"] = (
            f"{base_url.rstrip('/')}/{workspace}/ows?service=WFS&version=2.0.0&request=GetFeature&typeName={safe_layer}&maxFeatures=50&outputFormat=gml3{bbox_param}"
        )
        resource["geoserver_layer"] = layer

        p.toolkit.get_action("resource_update")(context, resource)

        return {"status": "success", "resource_id": resource_id}

    except Exception as e:
        log.error(f"Failed to cleanly proxy GeoJSON: {e}")
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
