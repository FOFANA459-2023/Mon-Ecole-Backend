"""The platform owner (a superuser) now runs every school; schools no longer have a "Super Administrator".

Members holding that role become Directors, the role is removed, and the Director role holds every
school permission.
"""

from django.db import migrations


def director_replaces_super_admin(apps, schema_editor):
    Role = apps.get_model("accounts", "Role")
    from apps.accounts.permissions_registry import ALL_CODES

    for director in Role.objects.filter(key="director"):
        director.permissions = sorted(ALL_CODES)
        director.name = "School Director / Principal"
        director.description = (
            "Runs the school: staff and access, students, classes, finances, grades and settings."
        )
        director.save(update_fields=["permissions", "name", "description"])
        for old in Role.objects.filter(school_id=director.school_id, key="super_admin"):
            for membership in old.memberships.all():
                membership.roles.add(director)
            old.delete()


class Migration(migrations.Migration):
    dependencies = [("accounts", "0004_invitation_fields")]

    operations = [migrations.RunPython(director_replaces_super_admin, migrations.RunPython.noop)]
