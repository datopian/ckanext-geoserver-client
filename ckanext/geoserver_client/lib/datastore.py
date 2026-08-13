# encoding: utf-8
"""GeoJSON -> CKAN DataStore loading, with native PostGIS geometry columns.

Trimmed port of ckanext-spatialdata's geofiles.py/postgis.py/db.py: same
approach (each GeoJSON feature's geometry is encoded to WKB directly - no
lat/lng/WKT column-name guessing needed, since a GeoJSON feature's geometry
is already structured data, unlike a flat CSV row), same DB helpers, but
with everything specific to *tabular* georeferencing (lat/lng/WKT field
names, cross-resource geometry linking, the CLI, the spatial-search API)
left out - not needed for "upload a GeoJSON, it ends up queryable".
"""

import logging
from contextlib import contextmanager
from typing import Generator, Iterable, Optional, Union

import geojson as geojson_lib
from ckan.plugins import toolkit
from geomet import wkb
from sqlalchemy import create_engine, sql, text
from sqlalchemy.engine import Connection, Engine, Row
from sqlalchemy.pool import NullPool
from sqlalchemy.sql import Select
from sqlalchemy.sql.elements import TextClause

log = logging.getLogger(__name__)

# Raw WKB geometry, kept alongside the friendlier PostGIS columns below (same
# convention as ckanext-spatialdata) so the original per-feature geometry is
# always recoverable even if the PostGIS columns are ever dropped/rebuilt.
WKB_FIELD_NAME = "geom_wkb"
GEOM_FIELD = "_geom"
GEOM_MERCATOR_FIELD = "_geom_webmercator"

BATCH_SIZE = 5000

DEFAULT_CONTEXT = {"ignore_auth": True}


# --- DB helpers (trimmed from ckanext-spatialdata's lib/db.py) -------------

_read_engine = None
_write_engine = None


def _get_engine(write: bool = False) -> Engine:
    if write:
        global _write_engine
        if _write_engine is None:
            _write_engine = create_engine(
                toolkit.config["ckan.datastore.write_url"], poolclass=NullPool
            )
        return _write_engine
    global _read_engine
    if _read_engine is None:
        _read_engine = create_engine(toolkit.config["ckan.datastore.read_url"])
    return _read_engine


@contextmanager
def get_connection(
    connection: Optional[Connection] = None,
    write: bool = False,
    raw: bool = False,
) -> Generator[Connection, None, None]:
    if connection:
        yield connection
    else:
        engine = _get_engine(write=write)
        with engine.begin() as new_connection:
            yield new_connection.connection if raw else new_connection


def _index_name(table: str, field: str, index_type: str) -> str:
    return f"{table}_{field}_{index_type}"


def _create_index(
    connection: Connection, table: str, field: str, index_type: str = "GIST"
):
    index_name = _index_name(table, field, index_type)
    connection.execute(text(f"""
            CREATE INDEX IF NOT EXISTS "{index_name}"
                ON "{table}"
             USING {index_type}("{field}")
             WHERE "{field}" IS NOT NULL;
            """))


def _index_exists(
    connection: Connection, table: str, field: str, index_type: str = "GIST"
) -> bool:
    query: Select = (
        sql.select([sql.func.count()])
        .select_from(sql.table("pg_indexes"))
        .where("indexname" == _index_name(table, field, index_type))
    )
    result = connection.execute(query).fetchone()
    return result[0] > 0


def _fields_exist(connection: Connection, table: str, fields: list) -> bool:
    query: Select = sql.select("*", from_obj=sql.table(table)).limit(0)
    all_fields = connection.execute(query).keys()
    return all(field in all_fields for field in fields)


def _create_geom_column(
    connection: Connection, table: str, field: str, geom_type: str, srid
) -> None:
    connection.execute(
        text(
            f"""ALTER TABLE "{table}" ADD COLUMN IF NOT EXISTS "{field}" geometry({geom_type}, {srid});"""
        )
    )


def _get_field_values(
    connection: Connection, resource_id: str, field: str, is_bytes: bool = False
) -> list:
    query: TextClause = text(f"""SELECT "{field}" FROM "{resource_id}";""")
    results: Iterable[Row] = connection.execute(query).fetchall()
    if is_bytes:
        return [bytes(item[0]) for item in results if item[0] is not None]
    return [item[0] for item in results if item[0] is not None]


# --- Geometry validation + WKB encoding (trimmed from geofiles.py) --------


def _validate_sub_geoms(geom_type, sub_geom_type, coords):
    valid_sub_geoms = []
    for sub_coords in coords:
        sub_geom = sub_geom_type(sub_coords)
        if sub_geom.is_valid:
            valid_sub_geoms.append(sub_geom)
    return geom_type(coords) if valid_sub_geoms else None


def _validate_geojson_geom(geojson_geo: dict):
    """Validate a GeoJSON geometry object. Returns it if valid, else None."""
    if not geojson_geo:
        return None
    geo_type = (geojson_geo.get("type") or "").lower()
    coords = geojson_geo.get("coordinates")

    data = None
    if geo_type == "point":
        data = geojson_lib.Point(coords)
    elif geo_type == "linestring":
        data = geojson_lib.LineString(coords)
    elif geo_type == "polygon":
        data = geojson_lib.Polygon(coords)
    elif geo_type == "multipoint":
        data = _validate_sub_geoms(geojson_lib.MultiPoint, geojson_lib.Point, coords)
    elif geo_type == "multilinestring":
        data = _validate_sub_geoms(
            geojson_lib.MultiLineString, geojson_lib.LineString, coords
        )
    elif geo_type == "multipolygon":
        data = _validate_sub_geoms(
            geojson_lib.MultiPolygon, geojson_lib.Polygon, coords
        )

    if data and data.is_valid:
        return data
    return None


def _geom_to_wkb(geojson_geo: dict):
    geo_data = _validate_geojson_geom(geojson_geo)
    if not geo_data:
        return None
    try:
        return wkb.dumps(geo_data)
    except Exception as e:
        log.warning("Failed to encode geometry to WKB: %s", e)
        return None


def _get_common_geom_type(wkb_values: list) -> str:
    """Find the common geometry type across a list of WKB values.

    Mirrors ckanext-spatialdata's get_common_geom_type (wkb-only, since
    that's the only source format this module deals with).
    """
    geom_types = list(
        {wkb.loads(v)["type"].upper() for v in wkb_values if v is not None}
    )
    if not geom_types:
        raise ValueError("At least one WKB value must be provided.")
    if len(geom_types) == 1:
        return geom_types[0]
    if len(geom_types) > 2:
        return "GEOMETRYCOLLECTION"
    ordered = sorted(geom_types, key=len)
    if ordered[0] in ordered[1]:
        return ordered[1]
    return "GEOMETRYCOLLECTION"


def _to_row(feature: dict, fields) -> dict:
    row = {field: feature["properties"].get(field) for field in fields}
    row[WKB_FIELD_NAME] = _geom_to_wkb(feature.get("geometry"))
    return row


# --- PostGIS column creation/population -----------------------------------


def _prep_geom_table(resource_id: str, geom_type: str) -> None:
    with get_connection(write=True) as c:
        if not _fields_exist(c, resource_id, [GEOM_FIELD, GEOM_MERCATOR_FIELD]):
            log.info("Creating PostGIS columns for %s.", resource_id)
            _create_geom_column(c, resource_id, GEOM_FIELD, geom_type, 4326)
            _create_geom_column(c, resource_id, GEOM_MERCATOR_FIELD, geom_type, 3857)
        if not (
            _index_exists(c, resource_id, GEOM_FIELD)
            and _index_exists(c, resource_id, GEOM_MERCATOR_FIELD)
        ):
            log.info("Creating PostGIS indexes for %s.", resource_id)
            _create_index(c, resource_id, GEOM_FIELD)
            _create_index(c, resource_id, GEOM_MERCATOR_FIELD)


def _populate_geom_columns_from_wkb(resource_id: str, geom_type: str) -> None:
    set_geom = f'ST_Force2D(ST_GeomFromWKB("{WKB_FIELD_NAME}", 4326))'
    if "multi" in geom_type.lower():
        set_geom = f"ST_Multi({set_geom})"

    source_sql = f"""
        SELECT _id
        FROM "{resource_id}"
        WHERE ("{GEOM_FIELD}" IS NULL OR "{GEOM_MERCATOR_FIELD}" IS NULL)
          AND "{WKB_FIELD_NAME}" IS NOT NULL
        ORDER BY _id
    """
    geom_update_sql = f"""
        UPDATE "{resource_id}"
        SET "{GEOM_FIELD}" = {set_geom}
        WHERE _id = %s
    """
    geom_mercator_update_sql = f"""
        UPDATE "{resource_id}"
        SET "{GEOM_MERCATOR_FIELD}" = ST_Transform("{GEOM_FIELD}", 3857)
        WHERE "{GEOM_FIELD}" IS NOT NULL AND _id = %s
    """

    with get_connection(write=True, raw=True) as c:
        read_cursor = c.cursor()
        write_cursor = c.cursor()
        read_cursor.execute(source_sql)
        count = 0
        while True:
            rows = read_cursor.fetchmany(BATCH_SIZE)
            if not rows:
                break
            for row in rows:
                write_cursor.execute(geom_update_sql, (row[0],))
            c.commit()
            for row in rows:
                count += 1
                write_cursor.execute(geom_mercator_update_sql, (row[0],))
            c.commit()
            log.debug("%s rows geocoded for %s.", count, resource_id)


def _progress(on_progress, message):
    log.info(message)
    if on_progress is not None:
        on_progress(message)


# --- Public entry point -----------------------------------------------------


def load_geojson_to_datastore(
    resource_id: str, get_resource_file, on_progress=None
) -> dict:
    """Parse a GeoJSON resource's features into DataStore records (one row
    per feature: its properties as columns, its geometry as a WKB column),
    load them via datastore_create, then add + populate native PostGIS
    geometry columns from that WKB.

    :param resource_id: the resource to load
    :param get_resource_file: callable(resource_id) -> (bytes-like file
        object). Takes this as a parameter rather than importing
        ckanext-geoserver-client's own fetch helper directly, so this module
        has no import-time dependency on the rest of the plugin.
    :param on_progress: optional callable(message) invoked at each major
        stage, so callers (the background job) can surface progress via
        their own status-tracking mechanism without this module needing to
        know anything about it.
    :returns: {"status": "success"} or {"status": "skipped", "reason": ...} -
        same convention as geoserver_ingest_geojson's return shape.
    """
    resource = toolkit.get_action("resource_show")(DEFAULT_CONTEXT, {"id": resource_id})

    _progress(on_progress, "Fetching resource file")
    with get_resource_file(resource_id) as f:
        import json

        geojson_doc = json.load(f)

    features = geojson_doc.get("features") or []
    if geojson_doc.get("type") != "FeatureCollection" or not features:
        # A bare Feature/geometry (not a FeatureCollection) isn't something
        # meaningful to load as a table of rows - nothing to do.
        reason = "Not a non-empty GeoJSON FeatureCollection"
        _progress(on_progress, f"Skipping datastore load: {reason}")
        return {"status": "skipped", "reason": reason}

    source_fields = set()
    for feature in features:
        source_fields |= set((feature.get("properties") or {}).keys())

    records = [_to_row(f, source_fields) for f in features if f.get("geometry")]
    if not records:
        reason = "No features with geometry"
        _progress(on_progress, f"Skipping datastore load: {reason}")
        return {"status": "skipped", "reason": reason}

    _progress(on_progress, f"Parsed {len(records)} feature(s) with geometry")

    fields = [
        {"id": k, "type": "bytea" if k == WKB_FIELD_NAME else "text"}
        for k in records[0].keys()
    ]

    # Replace any existing datastore table for this resource (e.g. a
    # re-upload/edit of the same GeoJSON).
    try:
        toolkit.get_action("datastore_info")(DEFAULT_CONTEXT, {"id": resource_id})
        toolkit.get_action("datastore_delete")(
            DEFAULT_CONTEXT, {"resource_id": resource_id, "force": True}
        )
    except toolkit.ObjectNotFound:
        pass

    toolkit.get_action("datastore_create")(
        DEFAULT_CONTEXT,
        {
            "resource_id": resource_id,
            "records": records,
            "fields": fields,
            "force": True,
        },
    )
    _progress(on_progress, f"Loaded {len(records)} record(s) into the DataStore")

    with get_connection() as c:
        wkb_values = _get_field_values(c, resource_id, WKB_FIELD_NAME, is_bytes=True)
    geom_type = _get_common_geom_type(wkb_values)

    _prep_geom_table(resource_id, geom_type)
    _populate_geom_columns_from_wkb(resource_id, geom_type)
    _progress(on_progress, f"Added PostGIS geometry columns ({geom_type})")

    # Views that require the DataStore (e.g. the "Table"/DataTables preview)
    # were skipped when this resource was first created, because that
    # happens synchronously at upload time - before this background job has
    # loaded anything, so datastore_active was still False then and nothing
    # re-checks it afterward on its own. create_datastore_views=True is
    # CKAN core's own documented way to run that second pass once the data
    # actually exists; ckan/logic/action/create.py's docstring for
    # resource_create_default_resource_views describes this exact case.
    toolkit.get_action("resource_create_default_resource_views")(
        DEFAULT_CONTEXT,
        {
            "resource": toolkit.get_action("resource_show")(
                DEFAULT_CONTEXT, {"id": resource_id}
            ),
            "create_datastore_views": True,
        },
    )
    _progress(on_progress, "Attached DataStore preview views")

    log.info(
        "Loaded GeoJSON resource %s into the datastore with PostGIS geometry columns.",
        resource_id,
    )
    return {"status": "success"}
