from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.permissions import HasSchoolPermission

from .serializers import SchoolSerializer
from .services import update_school


class CurrentSchoolView(APIView):
    """The school selected by the X-School-ID header: its profile and settings."""

    permission_classes = [IsAuthenticated, HasSchoolPermission]
    required_permissions = {"get": [], "patch": ["settings.manage"]}
    parser_classes = [JSONParser, MultiPartParser, FormParser]
    serializer_class = SchoolSerializer

    def get(self, request):
        return Response(SchoolSerializer(request.school, context={"request": request}).data)

    def patch(self, request):
        school = request.school
        serializer = SchoolSerializer(school, data=request.data, partial=True, context={"request": request})
        serializer.is_valid(raise_exception=True)
        data = dict(serializer.validated_data)
        settings_data = data.pop("settings", {})
        update_school(school, data=data, settings_data=settings_data, request=request)
        school.refresh_from_db()
        return Response(SchoolSerializer(school, context={"request": request}).data)
