"""Administration: departments, announcements (by role, scheduled, expiring) and the school calendar."""

from datetime import date, timedelta

import pytest
from django.utils import timezone

from apps.accounts.models import Role
from apps.administration.models import Announcement
from apps.audit.models import AuditLog

pytestmark = pytest.mark.django_db


@pytest.fixture
def director(school, make_member, client_for):
    return client_for(make_member(school, "director"), school)


@pytest.fixture
def teacher(school, make_member, client_for):
    return client_for(make_member(school, "teacher"), school)


class TestDepartments:
    def test_the_director_sets_up_departments_and_staff_join_them(
        self, school, director, teacher, make_staff
    ):
        head = make_staff("Mamadou", "Barry")
        response = director.post("/api/v1/departments/", {"name": "Sciences", "head": head.pk})
        assert response.status_code == 201, response.data
        department = response.data["id"]
        assert response.data["head_name"] == "Mamadou Barry"
        assert director.patch(f"/api/v1/staff/{head.pk}/", {"department": department}).status_code == 200
        rows = teacher.get("/api/v1/departments/").data
        assert [(r["name"], r["staff_count"]) for r in rows] == [("Sciences", 1)]
        staff = director.get("/api/v1/staff/", {"department": department}).data["results"]
        assert [s["department_name"] for s in staff] == ["Sciences"]
        # Teachers read them but cannot change them.
        assert teacher.post("/api/v1/departments/", {"name": "Langues"}).status_code == 403
        assert director.post("/api/v1/departments/", {"name": "Sciences"}).status_code == 400
        assert AuditLog.objects.filter(module="administration").exists()

    def test_a_head_from_another_school_is_refused(self, director, other_school):
        from apps.people.services import create_staff

        stranger = create_staff(other_school, data={"first_name": "A", "last_name": "B"})
        assert director.post("/api/v1/departments/", {"name": "X", "head": stranger.pk}).status_code == 400


class TestAnnouncements:
    def test_everyone_or_some_roles_read_what_is_published(
        self, school, director, teacher, make_member, client_for
    ):
        accountant = client_for(make_member(school, "accountant"), school)
        teachers = Role.objects.get(school=school, key="teacher")
        everyone = director.post(
            "/api/v1/announcements/",
            {"title": "Rentrée", "body": "Lundi 8 h.", "pinned": True},
            format="json",
        )
        assert everyone.status_code == 201, everyone.data
        director.post(
            "/api/v1/announcements/",
            {
                "title": "Conseil pédagogique",
                "body": "Jeudi.",
                "roles": [teachers.pk],
                "priority": "important",
            },
            format="json",
        )
        later = (timezone.now() + timedelta(days=2)).isoformat()
        director.post(
            "/api/v1/announcements/", {"title": "Plus tard", "body": "…", "publish_at": later}, format="json"
        )
        Announcement.objects.create(
            school=school, title="Vieille", body="…", expires_on=timezone.localdate() - timedelta(days=1)
        )

        assert [a["title"] for a in teacher.get("/api/v1/announcements/").data["results"]] == [
            "Rentrée",
            "Conseil pédagogique",
        ]
        assert [a["title"] for a in accountant.get("/api/v1/announcements/").data["results"]] == ["Rentrée"]
        # The director manages them all, scheduled and expired included.
        managed = director.get("/api/v1/announcements/", {"manage": "1"}).data["results"]
        assert {a["title"]: a["is_published"] for a in managed} == {
            "Rentrée": True,
            "Conseil pédagogique": True,
            "Plus tard": False,
            "Vieille": False,
        }
        assert teacher.post("/api/v1/announcements/", {"title": "x", "body": "y"}).status_code == 403

    def test_it_cannot_end_before_it_is_published(self, director):
        response = director.post(
            "/api/v1/announcements/",
            {"title": "x", "body": "y", "publish_at": "2026-10-20T08:00:00Z", "expires_on": "2026-10-10"},
            format="json",
        )
        assert response.status_code == 400 and "expires_on" in response.data["fields"]


class TestCalendar:
    def test_events_by_period_kind_and_level(self, school, director, teacher, level):
        from apps.academics.models import Level

        other = Level.objects.create(school=school, name="8ème année", order=8)
        events = [
            {
                "title": "Compositions T1",
                "kind": "exam",
                "start_date": "2026-12-14",
                "end_date": "2026-12-18",
                "levels": [level.pk],
            },
            {"title": "Noël", "kind": "holiday", "start_date": "2026-12-24", "end_date": "2027-01-02"},
            {
                "title": "Réunion parents 8e",
                "kind": "meeting",
                "start_date": "2026-11-05",
                "start_time": "15:00",
                "levels": [other.pk],
            },
        ]
        for event in events:
            response = director.post("/api/v1/calendar-events/", event, format="json")
            assert response.status_code == 201, response.data
        rows = teacher.get(
            "/api/v1/calendar-events/", {"date_from": "2026-12-01", "date_to": "2026-12-31"}
        ).data
        assert [r["title"] for r in rows] == ["Compositions T1", "Noël"]
        assert rows[0]["level_names"] == ["7ème année"]
        assert [r["title"] for r in teacher.get("/api/v1/calendar-events/", {"kind": "exam"}).data] == [
            "Compositions T1"
        ]
        by_level = teacher.get("/api/v1/calendar-events/", {"level": level.pk}).data
        assert [r["title"] for r in by_level] == ["Compositions T1", "Noël"]
        # One day when no end is given.
        meeting = next(r for r in teacher.get("/api/v1/calendar-events/").data if r["kind"] == "meeting")
        assert meeting["end_date"] == "2026-11-05"

    def test_the_end_comes_after_the_start(self, director):
        bad = director.post(
            "/api/v1/calendar-events/",
            {"title": "x", "start_date": "2026-11-05", "end_date": "2026-11-01"},
            format="json",
        )
        assert bad.status_code == 400
        bad_time = director.post(
            "/api/v1/calendar-events/",
            {"title": "x", "start_date": "2026-11-05", "start_time": "15:00", "end_time": "14:00"},
            format="json",
        )
        assert bad_time.status_code == 400

    def test_teachers_cannot_change_the_calendar(self, teacher):
        today = date.today().isoformat()
        assert (
            teacher.post("/api/v1/calendar-events/", {"title": "x", "start_date": today}).status_code == 403
        )


def test_official_documents_are_filed_under_the_school(school, director, teacher):
    from django.core.files.uploadedfile import SimpleUploadedFile

    pdf = SimpleUploadedFile("agrement.pdf", b"%PDF-1.4 test", content_type="application/pdf")
    response = director.post(
        "/api/v1/documents/",
        {"owner_type": "school", "owner_id": school.pk, "category": "official", "file": pdf},
        format="multipart",
    )
    assert response.status_code == 201, response.data
    assert len(director.get("/api/v1/documents/", {"owner_type": "school", "owner_id": school.pk}).data) == 1
    # Another id than the school's is not a school document.
    assert (
        director.get("/api/v1/documents/", {"owner_type": "school", "owner_id": school.pk + 1}).status_code
        == 404
    )
    # Teachers have no access to the administration's documents.
    assert (
        teacher.get("/api/v1/documents/", {"owner_type": "school", "owner_id": school.pk}).status_code == 403
    )


def test_a_malformed_date_filter_is_a_bad_request_not_a_crash(teacher):
    response = teacher.get("/api/v1/calendar-events/", {"date_from": "date_from", "date_to": "2026-13-45"})
    assert response.status_code == 400
    assert set(response.data["fields"]) == {"date_from", "date_to"}
