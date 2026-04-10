# ckanext-geoserver-client

A CKAN extension that provides a client for interacting with GeoServer natively from CKAN. It handles tasks such as workspace creation, uploading shapefiles, styling layers, and synchronizing GeoServer data automatically.

## Requirements

**System dependency (required):**

`ogr2ogr` must be installed on the CKAN server. It is used to convert GeoJSON to Shapefile before uploading to GeoServer:

```bash
# Debian/Ubuntu
apt-get install gdal-bin

# macOS
brew install gdal

# Alpine Linux
apk add gdal-bin
```

**Python dependency (optional):**

If you are using S3/MinIO for CKAN file storage (via `ckanext-s3filestore`), `boto3` is required for the extension to fetch resource files directly from S3:

```bash
pip install boto3==1.35.77
```

If `ckanext-s3filestore` is already installed, `boto3` will already be present. Note: newer versions of `boto3` may cause compatibility issues with `ckanext-s3filestore`.

## Installation

1. Activate your CKAN virtual environment:

    ```bash
    . /usr/lib/ckan/default/bin/activate
    ```

2. Install the extension:

    ```bash
    pip install -e git+https://github.com/datopian/ckanext-geoserver-client.git#egg=ckanext-geoserver-client
    ```

3. Add `geoserver_client` to `ckan.plugins` in your CKAN config file.

4. Restart CKAN.

## Configuration

Set the following in your CKAN config file (`production.ini`) or via environment variables:

```ini
# The REST API URL of your GeoServer instance.
# Default: http://localhost:8080/geoserver/rest
ckanext.geoserver_client.rest_url = http://geoserver:8080/geoserver/rest

# The admin username for GeoServer
# Default: admin
ckanext.geoserver_client.user = admin

# The admin password for GeoServer
# Default: geoserver
ckanext.geoserver_client.password = my_secret_password

# The target workspace in GeoServer where CKAN will publish layers
# Default: ckan
ckanext.geoserver_client.workspace = ckan

# The public-facing GeoServer URL used to build WMS/WFS links on resources
# Default: http://localhost:8080/geoserver
ckanext.geoserver_client.public_url = http://geoserver:8080/geoserver
```

**Environment variable equivalents** (for use with `ckanext-envvars`):

```bash
CKANEXT__GEOSERVER_CLIENT__REST_URL=http://geoserver:8080/geoserver/rest
CKANEXT__GEOSERVER_CLIENT__USER=admin
CKANEXT__GEOSERVER_CLIENT__PASSWORD=my_secret_password
CKANEXT__GEOSERVER_CLIENT__WORKSPACE=ckan
CKANEXT__GEOSERVER_CLIENT__PUBLIC_URL=http://geoserver:8080/geoserver
```

## How It Works

When a resource is **created or updated**, the plugin checks whether it is a GeoJSON resource. A resource is considered GeoJSON if either:

- Its **format field** is set to `GeoJSON` (case-insensitive), or
- Its **URL** ends with `.geojson`

If either condition is met, a background job is enqueued that:

1. Fetches the file (from local CKAN storage, S3/MinIO, or HTTP fallback)
2. Validates the content is valid GeoJSON
3. Converts it to a Shapefile using `ogr2ogr`
4. Uploads the Shapefile to GeoServer and publishes it as a layer
5. Optionally applies an SLD style if an `SLD` resource exists on the same dataset
6. Updates the resource with `wms_url`, `wfs_url`, and `geoserver_layer` fields

When a GeoJSON resource is **deleted**, the corresponding GeoServer layer is also removed.

Publishing only happens via the background worker. Make sure it is running:

```bash
ckan -c /etc/ckan/default/production.ini jobs worker
```

## CLI Commands

**Initialize GeoServer workspace:**

```bash
ckan -c /etc/ckan/default/production.ini geoserver init
```

**Publish a single resource:**

```bash
ckan -c /etc/ckan/default/production.ini geoserver publish <resource_id>
```

**Bulk publish all existing GeoJSON resources:**

```bash
ckan -c /etc/ckan/default/production.ini geoserver publish-all
```

## Development

```bash
git clone https://github.com/datopian/ckanext-geoserver-client.git
cd ckanext-geoserver-client
python setup.py develop  # or: pip install -e .
pip install -r dev-requirements.txt
```

## Tests

```bash
pytest --ckan-ini=test.ini

# With coverage:
pytest --ckan-ini=test.ini --cov=ckanext.geoserver_client --disable-warnings ckanext/geoserver_client/tests
```
