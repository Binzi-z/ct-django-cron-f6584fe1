from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('django_cron', '0003_cronjoblock'),
    ]

    operations = [
        migrations.AddField(
            model_name='cronjoblock',
            name='locked_at',
            field=models.DateTimeField(null=True, blank=True, db_index=True),
        ),
        migrations.AddField(
            model_name='cronjoblock',
            name='owner',
            field=models.CharField(max_length=255, blank=True, default=''),
        ),
    ]
