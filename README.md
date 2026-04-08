ckanext-geoserver-client
========================

A CKAN extension that provides a client for interacting with GeoServer natively from CKAN. It handles tasks such as workspace creation, uploading shapefiles, styling layers, and synchronizing GeoServer data automatically.

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

To configure the extension, set the following options in your CKAN configuration file (`production.ini` or via environment variables).

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

----------------------
Command Line Interface
----------------------

The extension provides several CKAN CLI commands under the `geoserver` command for managing GeoServer publishing.

**Initialize GeoServer Workspace**

Creates the configured GeoServer workspace if it does not already exist.

    ckan -c /etc/ckan/default/production.ini geoserver init

**Publish a Single Resource**

Downloads a CKAN GeoJSON resource, converts it to a shapefile, and publishes it natively as a layer inside GeoServer.

    ckan -c /etc/ckan/default/production.ini geoserver publish <resource_id>

**Bulk Publish Legacy Resources**

Finds all existing GeoJSON resources in the CKAN database and automatically processes/publishes them into GeoServer.

    ckan -c /etc/ckan/default/production.ini geoserver publish-all

------------------------
Development Installation
------------------------

To install ckanext-geoserver-client for development, activate your CKAN virtualenv and do::

    git clone https://github.com/datopian/ckanext-geoserver-client.git
    cd ckanext-geoserver-client
    python setup.py develop
    pip install -r dev-requirements.txt

-----------------
Running the Tests
-----------------

To run the tests, do::

    pytest --ckan-ini=test.ini 

To run the tests and produce a coverage report, first make sure you have coverage installed in your virtualenv (``pip install coverage``) then run::

    pytest --ckan-ini=test.ini --cov=ckanext.geoserver_client --disable-warnings ckanext/geoserver_client/tests