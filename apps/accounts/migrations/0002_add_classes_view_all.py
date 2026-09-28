"""Grant the new `classes.view_all` permission to existing built-in roles.

Only adds codes: permissions a school changed on its roles are left alone.
"""

from django.db import migrations

NEW_GRANTS = {
    "director": ["classes.view_all"],
    "admin_staff": ["classes.view_all"],
    "accountant": ["classes.view", "classes.view_all"],
}


def grant(apps, schema_editor):
    Role = apps.get_model("accounts", "Role")
    for key, codes in NEW_GRANTS.items():
        for role in Role.objects.filter(key=key, is_system=True):
            role.permissions = sorted(set(role.permissions) | set(codes))
            role.save(update_fields=["permissions"])


class Migration(migrations.Migration):
    dependencies = [("accounts", "0001_initial")]

    operations = [migrations.RunPython(grant, migrations.RunPython.noop)]
