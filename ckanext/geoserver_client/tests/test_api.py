import os
import zipfile
import subprocess
import uuid
import pytest
import requests
from ckanext.geoserver_client.lib.geoserver_api import GeoServerAPI


GEOJSON_DATA = (
    '{"type":"FeatureCollection","features":[{"type":"Feature",'
    '"geometry":{"type":"Point","coordinates":[13.0,55.6]},'
    '"properties":{"name":"Malmo"}}]}'
)


@pytest.mark.ckan_config("ckan.plugins", "geoserver_client")
@pytest.mark.usefixtures("with_plugins")
class TestGeoServerAPI:

    @pytest.fixture
    def layer_name(self):
        """Generate a unique layer name and guarantee GeoServer cleanup afterwards."""
        test_id = str(uuid.uuid4())[:8]
        name = f"api_test_layer_{test_id}"
        yield name
        try:
            GeoServerAPI().delete_layer(name)
        except Exception:
            pass

    @pytest.fixture
    def style_name(self, layer_name):
        """Generate a matching unique style name and guarantee GeoServer cleanup."""
        test_id = layer_name.rsplit("_", 1)[-1]
        name = f"test_api_style_{test_id}"
        yield name
        api = GeoServerAPI()
        try:
            api._request(
                "DELETE",
                f"workspaces/{api.workspace}/styles/{name}?purge=true&recurse=true",
            )
        except Exception:
            pass

    def _build_shapefile_zip(self, tmpdir, layer_name):
        geojson_path = os.path.join(tmpdir, "test.geojson")
        with open(geojson_path, "w") as f:
            f.write(GEOJSON_DATA)

        shp_dir = os.path.join(tmpdir, "shapefile_export")
        os.makedirs(shp_dir)
        shp_path = os.path.join(shp_dir, f"{layer_name}.shp")
        zip_path = os.path.join(tmpdir, f"{layer_name}.zip")

        subprocess.run(
            [
                "ogr2ogr",
                "-f", "ESRI Shapefile",
                shp_path,
                geojson_path,
                "-nln", layer_name,
                "-overwrite",
            ],
            check=True,
        )

        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zipf:
            for ext in [".shp", ".shx", ".dbf", ".prj", ".cpg"]:
                f_path = os.path.join(shp_dir, f"{layer_name}{ext}")
                if os.path.exists(f_path):
                    zipf.write(f_path, f"{layer_name}{ext}")

        return zip_path

    def test_api_lifecycle(self, layer_name, style_name, tmp_path):
        api = GeoServerAPI()

        status = api.ensure_workspace()
        assert status in ("exists", "created")

        zip_path = self._build_shapefile_zip(str(tmp_path), layer_name)

        # Shapefile upload
        res = api.upload_shapefile(layer_name, zip_path)
        assert res["layer"] == layer_name
        assert res["status"] == "published successfully"

        # Style upload + apply
        sld_body = (
            '<?xml version="1.0" encoding="ISO-8859-1"?>'
            '<StyledLayerDescriptor version="1.0.0" '
            'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
            'xsi:schemaLocation="http://www.opengis.net/sld StyledLayerDescriptor.xsd" '
            'xmlns="http://www.opengis.net/sld">'
            "<NamedLayer><Name>test</Name><UserStyle><Title>Test</Title>"
            "<FeatureTypeStyle><Rule><PolygonSymbolizer><Fill>"
            '<CssParameter name="fill">#2b8cbe</CssParameter>'
            "</Fill></PolygonSymbolizer></Rule></FeatureTypeStyle>"
            "</UserStyle></NamedLayer></StyledLayerDescriptor>"
        )
        api.upload_style(style_name, sld_body)

        try:
            api.set_layer_style(layer_name, style_name)
        except requests.exceptions.HTTPError as e:
            pytest.fail(f"Set layer style failed: {e.response.text}")

        # Bounding box
        bbox = api.get_bounding_box(layer_name)
        assert bbox is not None
        assert "13." in bbox

        # Explicit delete (fixture also does this as safety net)
        try:
            api.delete_layer(layer_name)
        except requests.exceptions.HTTPError as e:
            pytest.fail(f"Layer deletion failed: {e.response.text}")
