"""Grant the new finance permissions (fee set-up, invoice cancellation) to existing built-in roles.

Only adds codes: permissions a school changed on its roles are left alone.
"""

from django.db import migrations

NEW_CODES = ["finance.fees.manage", "finance.invoice.cancel"]
ROLES = ["super_admin", "director", "accountant"]


def grant(apps, schema_editor):
    Role = apps.get_model("accounts", "Role")
    for role in Role.objects.filter(key__in=ROLES, is_system=True):
        role.permissions = sorted(set(role.permissions) | set(NEW_CODES))
        role.save(update_fields=["permissions"])


class Migration(migrations.Migration):
    dependencies = [("accounts", "0002_add_classes_view_all")]

    operations = [migrations.RunPython(grant, migrations.RunPython.noop)]
