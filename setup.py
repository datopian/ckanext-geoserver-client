from setuptools import setup, find_packages

setup(
    name='ckanext-geoserver-client',
    version='1.0.0',
    description='A CKAN extension that provides a client for interacting with GeoServer.',
    license='AGPL',
    author='Datopian',
    author_email='',
    url='',
    packages=find_packages(exclude=['ez_setup', 'examples', 'tests']),
    namespace_packages=['ckanext', 'ckanext.geoserver_client'],
    include_package_data=True,
    zip_safe=False,
    install_requires=[
        'requests',
    ],
    entry_points='''
        [ckan.plugins]
        geoserver_client=ckanext.geoserver_client.plugin:GeoServerPlugin
        [ckan.click_command]
        geoserver_client=ckanext.geoserver_client.cli:geoserver
    ''',
)
