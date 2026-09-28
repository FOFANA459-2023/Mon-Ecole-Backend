import pytest
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile

from apps.accounts.models import Membership, Role
from apps.enrollments.services import enroll, withdraw
from apps.people.models import StaffMember, StudentGuardian

PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
    b"\x00\x00\x00\rIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
)


@pytest.mark.django_db
class TestStudents:
    def test_new_students_get_sequential_numbers(self, school, year, make_member, client_for):
        admin = make_member(school, "admin_staff")
        client = client_for(admin, school)
        first = client.post(
            "/api/v1/students/", {"first_name": "Awa", "last_name": "Diallo", "gender": "F"}, format="json"
        )
        second = client.post(
            "/api/v1/students/", {"first_name": "Sékou", "last_name": "Bah", "gender": "M"}, format="json"
        )
        assert first.status_code == 201, first.data
        assert first.data["student_number"] == "STU-2026-00001"
        assert second.data["student_number"] == "STU-2026-00002"

    def test_student_number_cannot_be_changed(self, school, make_student, make_member, client_for):
        student = make_student()
        admin = make_member(school, "super_admin")
        response = client_for(admin, school).patch(
            f"/api/v1/students/{student.pk}/", {"student_number": "HACK-1"}, format="json"
        )
        assert response.status_code == 400

    def test_filter_by_class(self, school, make_class, make_student, make_member, client_for):
        class_a, class_b = make_class("7ème A"), make_class("7ème B")
        in_a = make_student("Awa", "Diallo")
        enroll(in_a, class_a)
        enroll(make_student("Fanta", "Keita"), class_b)
        admin = make_member(school, "super_admin")
        response = client_for(admin, school).get("/api/v1/students/", {"class_group": class_a.pk})
        assert [row["id"] for row in response.data["results"]] == [in_a.pk]
        assert response.data["results"][0]["current_enrollment"]["class_name"] == "7ème A"

    def test_teacher_only_sees_students_of_their_classes(
        self, school, make_class, make_student, make_member, make_staff, client_for
    ):
        teacher_user = make_member(school, "teacher")
        staff = make_staff(user=teacher_user)
        mine, other = make_class("7ème A", class_teacher=staff), make_class("7ème B")
        visible = make_student("Awa", "Diallo")
        enroll(visible, mine)
        hidden = make_student("Fanta", "Keita")
        enroll(hidden, other)
        client = client_for(teacher_user, school)
        assert [row["id"] for row in client.get("/api/v1/students/").data["results"]] == [visible.pk]
        assert client.get(f"/api/v1/students/{hidden.pk}/").status_code == 404

    def test_search_includes_guardian_phone(self, school, make_student, make_member, client_for):
        from apps.people.services import link_guardian

        student = make_student()
        link_guardian(
            student,
            guardian_data={"first_name": "Mariama", "last_name": "Bah", "phone": "620112233"},
            relationship="mother",
        )
        admin = make_member(school, "super_admin")
        response = client_for(admin, school).get("/api/v1/students/", {"search": "620112233"})
        assert response.data["count"] == 1

    def test_archive_requires_leaving_the_class_first(
        self, school, make_class, make_student, make_member, client_for
    ):
        student = make_student()
        enrolment = enroll(student, make_class())
        admin = make_member(school, "super_admin")
        client = client_for(admin, school)
        assert client.post(f"/api/v1/students/{student.pk}/archive/").status_code == 400
        withdraw(enrolment, reason="Moved away")
        response = client.post(f"/api/v1/students/{student.pk}/archive/")
        assert response.status_code == 200
        assert response.data["status"] == "archived"

    def test_photo_must_be_an_image(self, school, make_student, make_member, client_for, settings, tmp_path):
        settings.MEDIA_ROOT = tmp_path
        student = make_student()
        client = client_for(make_member(school, "super_admin"), school)
        bad = SimpleUploadedFile("x.pdf", b"%PDF-1.4", content_type="application/pdf")
        assert (
            client.post(
                f"/api/v1/students/{student.pk}/photo/", {"file": bad}, format="multipart"
            ).status_code
            == 400
        )
        good = SimpleUploadedFile("p.png", PNG, content_type="image/png")
        response = client.post(f"/api/v1/students/{student.pk}/photo/", {"file": good}, format="multipart")
        assert response.status_code == 200
        assert response.data["photo_url"]

    def test_student_card_is_a_pdf(self, school, make_class, make_student, make_member, client_for):
        student = make_student()
        enroll(student, make_class())
        response = client_for(make_member(school, "teacher"), school)
        response = client_for(make_member(school, "super_admin", email="a@t.local"), school).get(
            f"/api/v1/students/{student.pk}/card/"
        )
        assert response.status_code == 200
        assert response["Content-Type"] == "application/pdf"
        assert response.content.startswith(b"%PDF")

    def test_export_to_excel_and_csv(self, school, make_student, make_member, client_for):
        make_student()
        client = client_for(make_member(school, "director"), school)
        xlsx = client.get("/api/v1/students/export/")
        assert xlsx.status_code == 200
        assert "spreadsheetml" in xlsx["Content-Type"]
        csv = client.get("/api/v1/students/export/", {"file_format": "csv"})
        assert "Diallo" in csv.content.decode("utf-8-sig")

    def test_teacher_cannot_export(self, school, make_member, client_for):
        assert (
            client_for(make_member(school, "teacher"), school).get("/api/v1/students/export/").status_code
            == 403
        )


@pytest.mark.django_db
class TestGuardians:
    def test_first_guardian_is_primary_and_a_new_primary_replaces_it(
        self, school, make_student, make_member, client_for
    ):
        student = make_student()
        client = client_for(make_member(school, "admin_staff"), school)
        father = client.post(
            f"/api/v1/students/{student.pk}/guardians/",
            {"first_name": "Alpha", "last_name": "Diallo", "phone": "620000001", "relationship": "father"},
            format="json",
        )
        assert father.status_code == 201, father.data
        assert father.data["is_primary"] is True
        mother = client.post(
            f"/api/v1/students/{student.pk}/guardians/",
            {
                "first_name": "Mariama",
                "last_name": "Bah",
                "phone": "620000002",
                "relationship": "mother",
                "is_primary": True,
            },
            format="json",
        )
        assert mother.data["is_primary"] is True
        assert StudentGuardian.objects.filter(student=student, is_primary=True).count() == 1

    def test_sibling_reuses_an_existing_guardian(self, school, make_student, make_member, client_for):
        from apps.people.services import link_guardian

        older, younger = make_student("Awa", "Diallo"), make_student("Ismaël", "Diallo")
        link = link_guardian(
            older,
            guardian_data={"first_name": "Alpha", "last_name": "Diallo", "phone": "620000001"},
            relationship="father",
        )
        client = client_for(make_member(school, "admin_staff"), school)
        response = client.post(
            f"/api/v1/students/{younger.pk}/guardians/",
            {"guardian_id": link.guardian_id, "relationship": "father"},
            format="json",
        )
        assert response.status_code == 201
        assert {s["id"] for s in response.data["guardian"]["students"]} == {older.pk, younger.pk}

    def test_guardian_needs_a_way_to_be_reached(self, school, make_student, make_member, client_for):
        student = make_student()
        response = client_for(make_member(school, "admin_staff"), school).post(
            f"/api/v1/students/{student.pk}/guardians/",
            {"first_name": "X", "last_name": "Y", "relationship": "guardian"},
            format="json",
        )
        assert response.status_code == 400

    def test_unlinking_the_primary_promotes_another(self, school, make_student, make_member, client_for):
        from apps.people.services import link_guardian

        student = make_student()
        primary = link_guardian(student, guardian_data={"first_name": "A", "last_name": "D", "phone": "1"})
        other = link_guardian(student, guardian_data={"first_name": "B", "last_name": "D", "phone": "2"})
        client = client_for(make_member(school, "admin_staff"), school)
        assert client.delete(f"/api/v1/students/{student.pk}/guardians/{primary.pk}/").status_code == 204
        other.refresh_from_db()
        assert other.is_primary is True


@pytest.mark.django_db
class TestStaff:
    def test_create_staff_with_auto_number(self, school, year, make_member, client_for):
        client = client_for(make_member(school, "director"), school)
        response = client.post(
            "/api/v1/staff/",
            {"first_name": "Ibrahima", "last_name": "Camara", "staff_type": "teacher", "position": "Maths"},
            format="json",
        )
        assert response.status_code == 201, response.data
        assert response.data["employee_number"] == "EMP-2026-0001"
        assert response.data["has_access"] is False

    def test_teacher_cannot_add_staff(self, school, make_member, client_for):
        response = client_for(make_member(school, "teacher"), school).post(
            "/api/v1/staff/", {"first_name": "x", "last_name": "y"}, format="json"
        )
        assert response.status_code == 403

    def test_grant_access_invites_and_links_the_account(
        self, school, make_staff, make_member, client_for, django_capture_on_commit_callbacks
    ):
        staff = make_staff(email="m.barry@test.local")
        admin = make_member(school, "super_admin")
        teacher_role = Role.objects.get(school=school, key="teacher")
        with django_capture_on_commit_callbacks(execute=True):
            response = client_for(admin, school).post(
                f"/api/v1/staff/{staff.pk}/grant-access/", {"role_ids": [teacher_role.pk]}, format="json"
            )
        assert response.status_code == 200, response.data
        assert response.data["has_access"] is True
        staff.refresh_from_db()
        assert Membership.objects.get(user=staff.user, school=school).roles.get() == teacher_role
        assert len(mail.outbox) == 1

    def test_grant_access_needs_an_email(self, school, make_staff, make_member, client_for):
        staff = make_staff()
        role = Role.objects.get(school=school, key="teacher")
        response = client_for(make_member(school, "super_admin"), school).post(
            f"/api/v1/staff/{staff.pk}/grant-access/", {"role_ids": [role.pk]}, format="json"
        )
        assert response.status_code == 400

    def test_archive_and_restore(self, school, make_staff, make_member, client_for):
        staff = make_staff()
        client = client_for(make_member(school, "director"), school)
        assert client.post(f"/api/v1/staff/{staff.pk}/archive/").data["status"] == "archived"
        assert client.post(f"/api/v1/staff/{staff.pk}/restore/").data["status"] == "active"
        assert StaffMember.objects.get(pk=staff.pk).archived_at is None
