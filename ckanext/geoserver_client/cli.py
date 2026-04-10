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
    temp_token = None
    sysadmin = None
    try:
        import ckan.model as model

        context = {
            "ignore_auth": True,
            "user": toolkit.get_action("get_site_user")({"ignore_auth": True}, {})[
                "name"
            ],
        }

        # Dynamically mint one Sysadmin Auth token for the entire bulk loop to prevent DB bloat
        sysadmin = (
            model.Session.query(model.User)
            .filter_by(sysadmin=True, state="active")
            .first()
        )
        temp_token = None
        if sysadmin:
            try:
                sysadmin_context = {
                    "model": model,
                    "session": model.Session,
                    "ignore_auth": True,
                    "user": sysadmin.name,
                }
                token_dict = toolkit.get_action("api_token_create")(
                    sysadmin_context,
                    {
                        "user": sysadmin.name,
                        "name": "geoserver_bulk_migration",
                        "expires_in": 7200,
                    },
                )
                temp_token = token_dict.get("token")
                context["api_token"] = temp_token
            except Exception as e:
                click.secho(
                    f"Warning: Failed to generate bulk SYSADMIN token: {e}", fg="yellow"
                )

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
        skip_count = 0

        for res_id in geojson_resources:
            try:
                click.secho(f"Processing {res_id}...", fg="yellow")

                result = toolkit.get_action("geoserver_ingest_geojson")(
                    context, {"resource_id": res_id}
                )

                if result.get("status") == "skipped":
                    skip_count += 1
                    click.secho(
                        f"Skipped: {res_id} ({result.get('reason', 'not GeoJSON')})",
                        fg="yellow",
                    )
                else:
                    success_count += 1
                    click.secho(
                        f"Success: {res_id} migrated and published!", fg="green"
                    )
            except Exception as e:
                click.secho(f"Failed on {res_id}: {e}", fg="red")

        click.secho(
            f"Finished: {success_count} published, {skip_count} skipped, "
            f"{len(geojson_resources) - success_count - skip_count} failed.",
            fg="green",
        )

    except Exception as e:
        click.secho(f"Error during bulk migration: {e}", fg="red")
    finally:
        # Revoke the bulk token immediately after the pipeline finishes
        if temp_token and sysadmin:
            try:
                toolkit.get_action("api_token_revoke")(
                    sysadmin_context, {"token": temp_token}
                )
                click.secho("Cleanup: Bulk Sysadmin token revoked.", fg="green")
            except Exception:
                pass
