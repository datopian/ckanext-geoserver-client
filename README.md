ckanext-geoserver-client
========================

A CKAN extension that provides a client for interacting with GeoServer natively from CKAN. It handles tasks such as workspace creation, uploading shapefiles, styling layers, and synchronizing GeoServer data automatically.

------------
Requirements
------------

**System dependency (required):**

``ogr2ogr`` must be installed on the CKAN server. It is used to convert GeoJSON to Shapefile before uploading to GeoServer::

    # Debian/Ubuntu
    apt-get install gdal-bin

    # macOS
    brew install gdal

**Python dependency (optional):**

If you are using S3/MinIO for CKAN file storage (via ``ckanext-s3filestore``), ``boto3`` is required for the extension to fetch resource files directly from S3::

    pip install "boto3>=1.4.4"

If ``ckanext-s3filestore`` is already installed, ``boto3`` will already be present.

------------
Installation
------------

To install ckanext-geoserver-client:

1. Activate your CKAN virtual environment, for example::

        . /usr/lib/ckan/default/bin/activate

2. Install the ckanext-geoserver-client Python package into your virtual environment::

        pip install -e git+https://github.com/datopian/ckanext-geoserver-client.git#egg=ckanext-geoserver-client

3. Add ``geoserver_client`` to the ``ckan.plugins`` setting in your CKAN config file (by default the config file is located at `/etc/ckan/default/production.ini`).

4. Restart CKAN. For example if you've deployed CKAN with Apache on Ubuntu:

        sudo service apache2 reload

---------------------
Configuration Options
---------------------

Set the following options in your CKAN configuration file (``production.ini``) or via environment variables.

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

**Environment variable equivalents** (for use with ``ckanext-envvars``)::

    CKANEXT__GEOSERVER_CLIENT__REST_URL=http://geoserver:8080/geoserver/rest
    CKANEXT__GEOSERVER_CLIENT__USER=admin
    CKANEXT__GEOSERVER_CLIENT__PASSWORD=my_secret_password
    CKANEXT__GEOSERVER_CLIENT__WORKSPACE=ckan
    CKANEXT__GEOSERVER_CLIENT__PUBLIC_URL=http://geoserver:8080/geoserver

-------------
How It Works
-------------

When a resource is **created or updated**, the plugin checks whether it is a GeoJSON resource. A resource is considered GeoJSON if either:

- Its **format field** is set to ``GeoJSON`` (case-insensitive), or
- Its **URL** ends with ``.geojson``

If either condition is met, a background job is enqueued that:

1. Fetches the file (from local CKAN storage, S3/MinIO, or HTTP fallback)
2. Validates the content is valid GeoJSON
3. Converts it to a Shapefile using ``ogr2ogr``
4. Uploads the Shapefile to GeoServer and publishes it as a layer
5. Optionally applies an SLD style if an ``SLD`` resource exists on the same dataset
6. Updates the resource with ``wms_url``, ``wfs_url``, and ``geoserver_layer`` fields

When a GeoJSON resource is **deleted**, the corresponding GeoServer layer is also removed.

Publishing only happens automatically via the background worker. Make sure the CKAN worker process is running::

    ckan -c /etc/ckan/default/production.ini jobs worker

----------------------
Command Line Interface
----------------------

The extension provides several CKAN CLI commands under the ``geoserver`` command for managing GeoServer publishing.

**Initialize GeoServer Workspace**

Creates the configured GeoServer workspace if it does not already exist::

    ckan -c /etc/ckan/default/production.ini geoserver init

**Publish a Single Resource**

Downloads a CKAN GeoJSON resource, converts it to a shapefile, and publishes it natively as a layer inside GeoServer::

    ckan -c /etc/ckan/default/production.ini geoserver publish <resource_id>

**Bulk Publish Legacy Resources**

Finds all existing GeoJSON resources in the CKAN database and automatically processes/publishes them into GeoServer::

    ckan -c /etc/ckan/default/production.ini geoserver publish-all

------------------------
Development Installation
------------------------

To install ckanext-geoserver-client for development, activate your CKAN virtualenv and do::

    git clone https://github.com/datopian/ckanext-geoserver-client.git
    cd ckanext-geoserver-client
    python setup.py develop
    pip install -r dev-requirements.txt

Note: ``pip install -e .`` is the modern equivalent of ``python setup.py develop``.

-----------------
Running the Tests
-----------------

To run the tests, do::

    pytest --ckan-ini=test.ini 

To run the tests and produce a coverage report, first make sure you have coverage installed in your virtualenv (``pip install coverage``) then run::

    pytest --ckan-ini=test.ini --cov=ckanext.geoserver_client --disable-warnings ckanext/geoserver_client/tests
