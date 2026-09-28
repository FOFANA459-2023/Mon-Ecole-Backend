from django.contrib import admin

from .models import School, SchoolSettings


class SchoolSettingsInline(admin.StackedInline):
    model = SchoolSettings
    can_delete = False


@admin.register(School)
class SchoolAdmin(admin.ModelAdmin):
    list_display = ["name", "code", "country", "currency", "status", "created_at"]
    list_filter = ["status", "country"]
    search_fields = ["name", "code"]
    inlines = [SchoolSettingsInline]

    def save_model(self, request, obj, form, change):
        if change:
            super().save_model(request, obj, form, change)
            return
        from .services import create_school

        fields = {
            f: form.cleaned_data[f]
            for f in form.cleaned_data
            if f not in {"name", "code"} and f in {fld.name for fld in obj._meta.fields}
        }
        created = create_school(name=obj.name, code=obj.code, created_by=request.user, **fields)
        obj.pk = created.pk
        obj.refresh_from_db()

    def save_related(self, request, form, formsets, change):
        if change:
            super().save_related(request, form, formsets, change)
