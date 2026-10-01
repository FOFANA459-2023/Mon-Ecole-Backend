"""Grant the new staff-attendance permission to the built-in Administrative Staff role.

Only adds the code: permissions a school changed on its roles are left alone. Directors hold every
permission already.
"""

from django.db import migrations

NEW_CODES = ["attendance.staff"]
ROLES = ["admin_staff"]


def grant(apps, schema_editor):
    Role = apps.get_model("accounts", "Role")
    for role in Role.objects.filter(key__in=ROLES, is_system=True):
        role.permissions = sorted(set(role.permissions) | set(NEW_CODES))
        role.save(update_fields=["permissions"])


class Migration(migrations.Migration):
    dependencies = [("accounts", "0006_user_manager")]

    operations = [migrations.RunPython(grant, migrations.RunPython.noop)]
