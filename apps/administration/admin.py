from django.contrib import admin

from .models import Announcement, CalendarEvent, Department

admin.site.register(Department)
admin.site.register(Announcement)
admin.site.register(CalendarEvent)
