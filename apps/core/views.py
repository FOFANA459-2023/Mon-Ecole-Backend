from django.db import connection
from django.http import JsonResponse


def healthz(request):
    try:
        connection.ensure_connection()
    except Exception:
        return JsonResponse({"status": "error", "database": "unavailable"}, status=503)
    return JsonResponse({"status": "ok"})
