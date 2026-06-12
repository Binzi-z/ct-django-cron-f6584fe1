# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import models, migrations


class Migration(migrations.Migration):

    dependencies = [
        ('django_cron', '0003_cronjoblock'),
    ]

    operations = [
        migrations.AddField(
            model_name='cronjoblock',
            name='locked_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='cronjoblock',
            name='token',
            field=models.CharField(blank=True, max_length=64, null=True),
        ),
    ]
