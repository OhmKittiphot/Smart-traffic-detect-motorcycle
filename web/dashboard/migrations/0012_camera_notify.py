from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('dashboard', '0011_user_units'),
    ]

    operations = [
        migrations.AddField(
            model_name='userpreferences',
            name='camera_notify',
            field=models.BooleanField(default=True),
        ),
    ]
