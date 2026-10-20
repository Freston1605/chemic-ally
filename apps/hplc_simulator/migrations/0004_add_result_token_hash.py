# Replay identity for server-signed result tokens (R015, S05-T03/T04).

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('hplc_simulator', '0003_add_analyte_response_fields'),
    ]

    operations = [
        migrations.AddField(
            model_name='userscore',
            name='result_token_hash',
            field=models.CharField(
                blank=True,
                help_text='sha256 of the signed result token this run came from',
                max_length=64,
                null=True,
                unique=True,
            ),
        ),
    ]
