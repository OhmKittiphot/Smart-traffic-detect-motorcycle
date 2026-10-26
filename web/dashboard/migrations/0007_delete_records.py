from django.contrib.auth.management import create_permissions
from django.db import migrations


def give_admins_delete(apps, schema_editor):
    """Admins may delete vehicle records from the violations page."""
    app_config = apps.get_app_config("dashboard")
    # The new permission is normally created after all migrations have run; create it now
    app_config.models_module = True
    create_permissions(app_config, apps=apps, verbosity=0)
    app_config.models_module = None

    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")
    admin = Group.objects.filter(name="Admin").first()
    if admin is not None:
        admin.permissions.add(Permission.objects.get(content_type__app_label="dashboard", codename="delete_records"))


class Migration(migrations.Migration):

    dependencies = [
        ("dashboard", "0006_manage_calibration"),
    ]

    operations = [
        migrations.AlterModelOptions(
            name="access",
            options={
                "managed": False,
                "default_permissions": (),
                "permissions": [
                    ("use_dashboard", "Can use the live view, change the video source and review events"),
                    ("view_records", "Can search records and view violation statistics"),
                    ("view_system_status", "Can view camera and system status"),
                    ("manage_calibration", "Can change the distances of, or remove, saved speed calibrations"),
                    ("delete_records", "Can delete vehicle records and their evidence photos"),
                ],
            },
        ),
        migrations.RunPython(give_admins_delete, migrations.RunPython.noop),
    ]
