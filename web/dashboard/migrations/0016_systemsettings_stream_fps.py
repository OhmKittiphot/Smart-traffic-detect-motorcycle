from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('dashboard', '0015_speedcalibration_reference_frame'),
    ]

    operations = [
        migrations.AddField(
            model_name='systemsettings',
            name='stream_fps',
            # The settings row already saved gets 25
            field=models.PositiveSmallIntegerField(default=25),
            preserve_default=False,
        ),
    ]
