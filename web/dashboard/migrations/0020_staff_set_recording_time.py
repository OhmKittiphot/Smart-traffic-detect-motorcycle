from django.db import migrations


def give_staff_recording_time(apps, schema_editor):
    """Staff set when the video file they play was recorded (live view, next to the fps menu)."""
    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")
    staff = Group.objects.filter(name="Staff").first()
    if staff is not None:
        staff.permissions.add(Permission.objects.get(content_type__app_label="dashboard", codename="set_recording_time"))


class Migration(migrations.Migration):

    dependencies = [
        ('dashboard', '0019_vehiclerecord_saved_at'),
    ]

    operations = [
        migrations.RunPython(give_staff_recording_time, migrations.RunPython.noop),
    ]
