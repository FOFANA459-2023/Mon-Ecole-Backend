import pytest
from django.core import mail

from apps.accounts.models import Membership, User


def register_payload(**extra):
    return {
        "name": "Collège Espoir",
        "code": "Espoir",
        "country": "gn",
        "currency": "gnf",
        "timezone": "Africa/Conakry",
        "default_language": "en",
        "director": {"first_name": "Fanta", "last_name": "Camara", "email": "Fanta@Espoir.test"},
        **extra,
    }


@pytest.mark.django_db
class TestPlatformSchools:
    def test_owner_registers_a_school_with_its_director(
        self, owner, client_for, django_capture_on_commit_callbacks
    ):
        with django_capture_on_commit_callbacks(execute=True):
            response = client_for(owner).post("/api/v1/platform/schools/", register_payload(), format="json")
        assert response.status_code == 201, response.data
        data = response.data
        assert (data["code"], data["country"], data["currency"], data["default_language"]) == (
            "espoir",
            "GN",
            "GNF",
            "en",
        )
        assert data["directors"] == [
            {
                "id": data["directors"][0]["id"],
                "full_name": "Fanta Camara",
                "email": "fanta@espoir.test",
                "phone": "",
                "account_status": "pending",
            }
        ]
        director = User.objects.get(email="fanta@espoir.test")
        # The Director's screens and invitation follow the school's language.
        assert director.language == "en"
        assert [r.key for r in Membership.objects.get(user=director).roles.all()] == ["director"]
        assert len(mail.outbox) == 1
        assert "Temporary password" in mail.outbox[0].body

    def test_codes_are_unique(self, owner, school, client_for):
        response = client_for(owner).post(
            "/api/v1/platform/schools/", register_payload(code=school.code), format="json"
        )
        assert response.status_code == 400
        assert "code" in response.data["fields"]

    def test_nothing_is_created_when_the_director_is_invalid(self, owner, client_for):
        payload = register_payload(director={"first_name": "", "last_name": "x", "email": "not-an-email"})
        response = client_for(owner).post("/api/v1/platform/schools/", payload, format="json")
        assert response.status_code == 400
        assert not User.objects.filter(email="not-an-email").exists()

    def test_owner_lists_every_school(self, owner, school, other_school, make_member, client_for):
        make_member(school, "director")
        response = client_for(owner).get("/api/v1/platform/schools/")
        assert [s["code"] for s in response.data["results"]] == ["alpha", "beta"]
        alpha = response.data["results"][0]
        assert alpha["member_count"] == 1
        assert alpha["directors"][0]["account_status"] == "active"

    def test_owner_acts_in_any_school_as_a_director(self, owner, school, client_for):
        client = client_for(owner, school)
        assert client.get("/api/v1/users/").status_code == 200
        assert client.get("/api/v1/invoices/").status_code == 200

    def test_suspend_and_reactivate(self, owner, school, make_member, client_for):
        teacher = make_member(school, "teacher")
        client = client_for(owner)
        response = client.post(f"/api/v1/platform/schools/{school.pk}/status/", {"status": "suspended"})
        assert response.data["status"] == "suspended"
        assert client_for(teacher, school).get("/api/v1/school/").status_code == 404
        client.post(f"/api/v1/platform/schools/{school.pk}/status/", {"status": "active"})
        assert client_for(teacher, school).get("/api/v1/school/").status_code == 200

    def test_resend_the_directors_invitation(self, owner, client_for, django_capture_on_commit_callbacks):
        with django_capture_on_commit_callbacks(execute=True):
            created = client_for(owner).post("/api/v1/platform/schools/", register_payload(), format="json")
        with django_capture_on_commit_callbacks(execute=True):
            response = client_for(owner).post(
                f"/api/v1/platform/schools/{created.data['id']}/resend-invitation/"
            )
        assert response.status_code == 204
        assert len(mail.outbox) == 2

    def test_directors_cannot_see_the_platform(self, school, make_member, client_for):
        client = client_for(make_member(school, "director"))
        assert client.get("/api/v1/platform/schools/").status_code == 403
        assert client.post("/api/v1/platform/schools/", register_payload(), format="json").status_code == 403
