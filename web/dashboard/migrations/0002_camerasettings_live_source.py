from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('dashboard', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='camerasettings',
            name='live_source',
            field=models.CharField(blank=True, max_length=64),
        ),
    ]
