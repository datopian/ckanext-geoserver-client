import pytest
from unittest.mock import patch
from ckan import plugins as p
from ckan.tests import factories


GEOJSON_DATA = (
    '{"type":"FeatureCollection","features":[{"type":"Feature",'
    '"geometry":{"type":"Point","coordinates":[13.0,55.6]},'
    '"properties":{"name":"Malmo"}}]}'
)


@pytest.mark.ckan_config("ckan.plugins", "geoserver_client")
@pytest.mark.usefixtures("with_plugins")
class TestGeoServerActions:

    @pytest.fixture
    def geojson_resource(self):
        """Create a dataset + GeoJSON resource for testing, cleaned up automatically."""
        dataset = factories.Dataset()
        resource = factories.Resource(
            package_id=dataset["id"],
            format="geojson",
            url="http://fake.url/test.geojson",
        )
        yield resource
        # Teardown: purge the dataset so nothing lingers in the test DB
        user = factories.User(sysadmin=True)
        context = {"user": user["name"], "ignore_auth": True}
        try:
            p.toolkit.get_action("dataset_purge")(context, {"id": dataset["id"]})
        except Exception:
            pass

    @pytest.fixture
    def mock_fetch(self, tmp_path):
        """
        Mock _fetch_resource_file so tests never touch the network, local disk,
        or S3. Writes the sample GeoJSON to dest_path directly.
        """
        def _write_geojson(resource, dest_path):
            with open(dest_path, "w", encoding="utf-8") as f:
                f.write(GEOJSON_DATA)

        with patch(
            "ckanext.geoserver_client.logic.action._fetch_resource_file",
            side_effect=_write_geojson,
        ):
            yield

    @pytest.fixture
    def cleanup_layer(self):
        """Remove a GeoServer layer after the test, even if the test fails."""
        created_ids = []
        yield created_ids
        from ckanext.geoserver_client.lib.geoserver_api import GeoServerAPI
        for resource_id in created_ids:
            try:
                GeoServerAPI().delete_layer(resource_id)
            except Exception:
                pass

    def test_geoserver_ingest_geojson(self, geojson_resource, mock_fetch, cleanup_layer):
        user = factories.User(sysadmin=True)
        context = {"user": user["name"], "ignore_auth": True}
        resource = geojson_resource
        cleanup_layer.append(resource["id"])

        res = p.toolkit.get_action("geoserver_ingest_geojson")(
            context, {"resource_id": resource["id"]}
        )

        assert res["status"] == "success"
        assert res["resource_id"] == resource["id"]

        updated_resource = p.toolkit.get_action("resource_show")(
            context, {"id": resource["id"]}
        )
        assert "wms_url" in updated_resource
        assert updated_resource["geoserver_layer"] == f"ckan:{resource['id']}"

    def test_geoserver_setup_workspace(self):
        user = factories.User(sysadmin=True)
        context = {"user": user["name"], "ignore_auth": True}

        res = p.toolkit.get_action("geoserver_setup_workspace")(context, {})
        assert res["success"] is True
        assert "Workspace is totally healthy" in res["message"]
