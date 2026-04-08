import pytest
from unittest.mock import patch
from ckan import plugins as p
from ckan.tests import factories


@pytest.mark.ckan_config("ckan.plugins", "geoserver")
@pytest.mark.usefixtures("with_plugins")
class TestGeoServerActions:

    @pytest.fixture
    def mock_requests_get(self):
        with patch("ckanext.geoserver_client.logic.action.requests.get") as mock_get:
            yield mock_get

    def test_geoserver_ingest_geojson(self, mock_requests_get, tmpdir):
        user = factories.User(sysadmin=True)
        context = {"user": user["name"], "ignore_auth": True}

        # Test creation of a dummy package/resource inside CKAN natively
        dataset = factories.Dataset()
        resource = factories.Resource(
            package_id=dataset["id"],
            format="geojson",
            url="http://fake.url/test.geojson",
        )

        # Setup mocking so the worker doesn't try to actually connect to `http://fake.url` over the wire!
        geojson_data = '{"type":"FeatureCollection","features":[{"type":"Feature","geometry":{"type":"Point","coordinates":[13.0,55.6]},"properties":{"name":"Malmo"}}]}'

        class MockResponse:
            def __init__(self, content):
                self.content = content

            def iter_content(self, chunk_size):
                yield self.content

            def raise_for_status(self):
                pass

        mock_requests_get.return_value = MockResponse(geojson_data.encode("utf-8"))

        # Fire the standalone pipeline!
        res = p.toolkit.get_action("geoserver_ingest_geojson")(
            context, {"resource_id": resource["id"]}
        )

        assert res["status"] == "success"
        assert res["resource_id"] == resource["id"]

        # Pull it immediately out of DB again, confirm everything sync'd.
        updated_resource = p.toolkit.get_action("resource_show")(
            context, {"id": resource["id"]}
        )
        assert "wms_url" in updated_resource
        assert updated_resource["geoserver_layer"] == f"ckan:{resource['id']}"

        # Cleanup
        from ckanext.geoserver_client.lib.geoserver_api import GeoServerAPI

        try:
            GeoServerAPI().delete_layer(resource["id"])
        except:
            pass

    def test_geoserver_setup_workspace(self):
        user = factories.User(sysadmin=True)
        context = {"user": user["name"], "ignore_auth": True}

        res = p.toolkit.get_action("geoserver_setup_workspace")(context, {})
        assert res["success"] is True
        assert "Workspace is totally healthy" in res["message"]
