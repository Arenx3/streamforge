from django.db import migrations, models
from django.db.models import F


def mark_existing_accounts_verified(apps, schema_editor):
    """Accounts from before verification existed must not lose access or be cleaned up."""
    user_model = apps.get_model("accounts", "User")
    user_model.objects.filter(email_verified_at__isnull=True).update(
        email_verified_at=F("date_joined")
    )


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="email_verified_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.RunPython(mark_existing_accounts_verified, migrations.RunPython.noop),
    ]
