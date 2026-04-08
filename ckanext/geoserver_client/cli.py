import click
from ckan.plugins import toolkit


@click.group()
def geoserver():
    """GeoServer REST API Management Commands"""
    pass


@geoserver.command()
def init():
    """Initialize GeoServer workspace and datastore."""
    try:
        context = {"ignore_auth": True}
        result = toolkit.get_action("geoserver_setup_workspace")(context, {})
        click.secho(
            f"Success: {result.get('message', 'Workspace initialized.')}", fg="green"
        )
    except Exception as e:
        click.secho(f"Error initializing workspace: {e}", fg="red")


@geoserver.command()
@click.argument("resource_id")
def publish(resource_id):
    """Ingest and publish a single CKAN GeoJSON resource to GeoServer."""
    try:
        context = {"ignore_auth": True}
        result = toolkit.get_action("geoserver_ingest_geojson")(
            context, {"resource_id": resource_id}
        )
        click.secho(
            f"Success: Resource {result.get('resource_id', resource_id)} published to GeoServer.",
            fg="green",
        )
    except Exception as e:
        click.secho(f"Error publishing resource: {e}", fg="red")


@geoserver.command("publish-all")
def publish_all():
    """Ingest and Publish all existing GeoJSON resources to GeoServer."""
    try:
        import ckan.model as model

        context = {
            "ignore_auth": True,
            "user": toolkit.get_action("get_site_user")({"ignore_auth": True}, {})[
                "name"
            ],
        }

        geojson_resources = []
        # Stream results instead of fully hydrating all ORM models to prevent memory exhaustion
        query = (
            model.Session.query(
                model.Resource.id, model.Resource.format, model.Resource.url
            )
            .filter_by(state="active")
            .yield_per(1000)
        )
        for res_id, fmt, url in query:
            fmt = (fmt or "").lower()
            url = (url or "").lower()
            # Catch legacy resources based on format or URL regardless of Datastore
            if fmt == "geojson" or url.endswith(".geojson"):
                geojson_resources.append(res_id)

        click.secho(
            f"Found {len(geojson_resources)} existing GeoJSON resources. Starting migration...",
            fg="blue",
        )
        success_count = 0

        for res_id in geojson_resources:
            try:
                click.secho(f"Processing {res_id}...", fg="yellow")

                toolkit.get_action("geoserver_ingest_geojson")(
                    context, {"resource_id": res_id}
                )

                success_count += 1
                click.secho(f"Success: {res_id} migrated and published!", fg="green")
            except Exception as e:
                click.secho(f"Failed on {res_id}: {e}", fg="red")

        click.secho(
            f"Finished migrating {success_count}/{len(geojson_resources)} legacy GeoJSONs.",
            fg="green",
        )

    except Exception as e:
        click.secho(f"Error during bulk migration: {e}", fg="red")
