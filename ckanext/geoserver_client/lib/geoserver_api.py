import logging
import requests
from requests.auth import HTTPBasicAuth
from ckan.plugins import toolkit

log = logging.getLogger(__name__)


class GeoServerAPI(object):
    def __init__(self):
        self.url = toolkit.config.get(
            "ckanext.geoserver_client.rest_url", "http://localhost:8080/geoserver/rest"
        )
        self.user = toolkit.config.get("ckanext.geoserver_client.user", "admin")
        self.password = toolkit.config.get(
            "ckanext.geoserver_client.password", "geoserver"
        )
        self.workspace = toolkit.config.get(
            "ckanext.geoserver_client.workspace", "ckan"
        )

    def _request(
        self,
        method,
        endpoint,
        json_data=None,
        file_data=None,
        content_type="application/json",
    ):
        url = f"{self.url.rstrip('/')}/{endpoint.lstrip('/')}"
        auth = HTTPBasicAuth(self.user, self.password)
        headers = {"Accept": "application/json", "Content-type": content_type}

        response = requests.request(
            method,
            url,
            auth=auth,
            headers=headers,
            json=json_data,
            data=file_data,
            timeout=30,
        )
        response.raise_for_status()

        if response.content and response.status_code not in (201, 204):
            try:
                return response.json()
            except ValueError:
                return response.text
        return {}

    def ensure_workspace(self):
        try:
            self._request("GET", f"workspaces/{self.workspace}")
            return "exists"
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 404:
                payload = {"workspace": {"name": self.workspace}}
                self._request("POST", "workspaces", json_data=payload)
                return "created"
            else:
                log.error(f"GeoServer workspace check failed: {e.response.text}")
                raise

    def upload_shapefile(self, resource_id, zip_path):
        """
        Uploads a zipped shapefile directly to GeoServer
        """
        self.ensure_workspace()

        endpoint = f"workspaces/{self.workspace}/datastores/{resource_id}/file.shp"

        with open(zip_path, "rb") as f:
            try:
                self._request(
                    "PUT", endpoint, file_data=f, content_type="application/zip"
                )
            except requests.exceptions.HTTPError as e:
                log.error(f"GeoServer Shapefile upload failed: {e.response.text}")
                raise

        return {"layer": resource_id, "status": "published successfully"}

    def upload_style(self, style_name, sld_body):
        """Builds a permanent SLD style directly into the GeoServer engine catalog."""
        self.ensure_workspace()

        headers = {"Content-type": "application/vnd.ogc.sld+xml"}
        auth = HTTPBasicAuth(self.user, self.password)
        base = self.url.rstrip("/")
        payload = sld_body.encode("utf-8")

        # Try to create the style via POST
        post_url = f"{base}/workspaces/{self.workspace}/styles?name={style_name}"
        res = requests.post(post_url, auth=auth, headers=headers, data=payload)

        if res.status_code < 400:
            return True

        # Style already exists — GeoServer returns 403, 409, or 500 depending on version.
        # Fall back to PUT to update the existing style.
        # Only fall back on "already exists" codes, not 400 Bad Request (invalid SLD).
        if res.status_code not in (403, 409, 500):
            log.error(
                f"Style POST failed with {res.status_code} for {style_name!r}: {res.text}"
            )
            res.raise_for_status()
        log.debug(f"Style POST {res.status_code} for {style_name!r}, updating via PUT")
        put_url = f"{base}/workspaces/{self.workspace}/styles/{style_name}"
        res_put = requests.put(put_url, auth=auth, headers=headers, data=payload)

        if res_put.status_code >= 400:
            log.error(f"Failed to PUT style {style_name!r}: {res_put.text}")
            res_put.raise_for_status()

        return True

    def set_layer_style(self, resource_id, style_name):
        endpoint = f"workspaces/{self.workspace}/layers/{resource_id}"
        payload = {
            "layer": {"defaultStyle": {"name": style_name, "workspace": self.workspace}}
        }
        self._request("PUT", endpoint, json_data=payload)

    def update_layer_title(self, resource_id, title):
        endpoint = (
            f"workspaces/{self.workspace}/datastores/{resource_id}"
            f"/featuretypes/{resource_id}"
        )
        try:
            self._request("PUT", endpoint, json_data={"featureType": {"title": title}})
        except Exception as e:
            log.warning(f"Could not update layer title for {resource_id}: {e}")

    def get_bounding_box(self, resource_id):
        endpoint = f"workspaces/{self.workspace}/datastores/{resource_id}/featuretypes/{resource_id}.json"

        try:
            res = self._request("GET", endpoint)
            box = res.get("featureType", {}).get("latLonBoundingBox", {})

            if box and "minx" in box:
                return f"{box['minx']},{box['miny']},{box['maxx']},{box['maxy']}"

        except Exception as e:
            log.warning(f"Could not retrieve bbox for layer {resource_id}: {e}")

        return None

    def delete_layer(self, resource_id):
        try:
            self._request(
                "DELETE",
                f"workspaces/{self.workspace}/datastores/{resource_id}?recurse=true",
            )
        except requests.exceptions.HTTPError as e:
            # 404 means it's already deleted or never existed, which is fine
            if e.response.status_code != 404:
                log.error(f"GeoServer datastore deletion failed: {e.response.text}")
                raise
