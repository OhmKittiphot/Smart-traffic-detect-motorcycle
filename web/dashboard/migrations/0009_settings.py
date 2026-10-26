import django.db.models.deletion
from django.conf import settings
from django.contrib.auth.management import create_permissions
from django.db import migrations, models


def give_admins_settings(apps, schema_editor):
    """Admins may change the system settings."""
    app_config = apps.get_app_config("dashboard")
    # The new permission is normally created after all migrations have run; create it now
    app_config.models_module = True
    create_permissions(app_config, apps=apps, verbosity=0)
    app_config.models_module = None

    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")
    admin = Group.objects.filter(name="Admin").first()
    if admin is not None:
        admin.permissions.add(Permission.objects.get(content_type__app_label="dashboard", codename="manage_settings"))


class Migration(migrations.Migration):

    dependencies = [
        ('dashboard', '0008_vehiclerecord_plate_snapshot'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AlterModelOptions(
            name='access',
            options={'default_permissions': (), 'managed': False, 'permissions': [('use_dashboard', 'Can use the live view, change the video source and review events'), ('view_records', 'Can search records and view violation statistics'), ('view_system_status', 'Can view camera and system status'), ('manage_calibration', 'Can change the distances of, or remove, saved speed calibrations'), ('delete_records', 'Can delete vehicle records and their evidence photos'), ('manage_settings', 'Can change the violation rules and how long records are kept')]},
        ),
        migrations.CreateModel(
            name='SystemSettings',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('speed_limit_kmh', models.PositiveSmallIntegerField(default=80)),
                ('max_riders', models.PositiveSmallIntegerField(default=2)),
                ('record_retention_days', models.PositiveIntegerField()),
                ('photo_retention_days', models.PositiveIntegerField()),
                ('media_max_mb', models.PositiveIntegerField()),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('updated_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'System settings',
                'verbose_name_plural': 'System settings',
            },
        ),
        migrations.CreateModel(
            name='UserPreferences',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('table_filter', models.CharField(choices=[('all', 'All'), ('normal', 'Normal'), ('violation', 'Violation')], default='all', max_length=10)),
                ('seek_step_s', models.PositiveSmallIntegerField(default=10)),
                ('show_fps_chart', models.BooleanField(default=True)),
                ('violation_sound', models.BooleanField(default=False)),
                ('user', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='preferences', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'User preferences',
                'verbose_name_plural': 'User preferences',
            },
        ),
        migrations.RunPython(give_admins_settings, migrations.RunPython.noop),
    ]
