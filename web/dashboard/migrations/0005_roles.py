from django.contrib.auth.hashers import make_password
from django.contrib.auth.management import create_permissions
from django.db import migrations, models

# Default roles (groups) and their permissions as (app_label, codename); Admins can change them in /admin/
ROLES = {
    # Live view, video source, speed calibration, plate search and reviewing events
    "Staff": [("dashboard", "use_dashboard"), ("dashboard", "view_records")],
    # Only: search + violation statistics, user accounts and roles, camera and system status
    "Admin": [("dashboard", "view_records"), ("dashboard", "view_system_status")]
             + [("auth", f"{action}_{model}") for model in ("user", "group")
                for action in ("add", "change", "delete", "view")],
}


def create_roles(apps, schema_editor):
    """Admin and Staff groups; existing superusers become (non-super) Admins, since a superuser has every
    permission; other existing accounts become Staff. Plus a default staff account (staff / staff)."""
    # Permissions are normally created after all migrations have run; create them now so groups can get them
    for app_config in apps.get_app_configs():
        app_config.models_module = True
        create_permissions(app_config, apps=apps, verbosity=0)
        app_config.models_module = None

    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")
    User = apps.get_model("auth", "User")
    groups = {}
    for name, perms in ROLES.items():
        groups[name], _ = Group.objects.get_or_create(name=name)
        groups[name].permissions.set([Permission.objects.get(content_type__app_label=app_label, codename=codename)
                                      for app_label, codename in perms])

    for user in User.objects.all():
        if user.is_superuser:
            user.is_superuser = False
            user.save(update_fields=["is_superuser"])
            groups["Admin"].user_set.add(user)
        else:
            groups["Staff"].user_set.add(user)
    if not User.objects.filter(username="staff").exists():
        # is_staff=False: cannot log in to Django admin
        user = User.objects.create(username="staff", password=make_password("staff"), is_staff=False)
        user.groups.add(groups["Staff"])


class Migration(migrations.Migration):

    dependencies = [
        ("dashboard", "0004_vehiclerecord_review"),
        ("auth", "0012_alter_user_first_name_max_length"),
        ("contenttypes", "0002_remove_content_type_name"),
    ]

    operations = [
        migrations.CreateModel(
            name="Access",
            fields=[("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID"))],
            options={
                "managed": False,
                "default_permissions": (),
                "permissions": [
                    ("use_dashboard", "Can use the live view, change the video source and review events"),
                    ("view_records", "Can search records and view violation statistics"),
                    ("view_system_status", "Can view camera and system status"),
                ],
            },
        ),
        migrations.RunPython(create_roles, migrations.RunPython.noop),
    ]
