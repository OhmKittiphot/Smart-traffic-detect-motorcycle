from django.db import migrations


class Migration(migrations.Migration):
    """The live view's new-violation alert is a desktop notification instead of a beep."""

    dependencies = [
        ("dashboard", "0009_settings"),
    ]

    operations = [
        migrations.RenameField(
            model_name="userpreferences",
            old_name="violation_sound",
            new_name="violation_notify",
        ),
    ]
