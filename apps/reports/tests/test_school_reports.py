"""School reports (/reports/school/{key}/): enrolment, classes, teachers, attendance, student statistics."""

from datetime import date

import pytest

from apps.academics.models import ClassSubject, Subject
from apps.attendance.models import AttendanceRecord, ClassRegister, StaffAttendance
from apps.audit.models import AuditLog
from apps.enrollments import services as enrollment_services

pytestmark = pytest.mark.django_db
URL = "/api/v1/reports/school/"


@pytest.fixture
def data(school, year, make_class, make_student, make_staff, make_member, client_for):
    class_a, class_b = make_class("7ème A", capacity=40), make_class("7ème B")
    barry = make_staff("Mamadou", "Barry", staff_type="teacher")
    class_a.class_teacher = barry
    class_a.save()
    maths = Subject.objects.create(school=school, name="Mathématiques", code="MATH")
    ClassSubject.objects.create(
        school=school, class_group=class_a, subject=maths, teacher=barry, coefficient=4
    )
    awa = enrollment_services.enroll(
        make_student("Awa", "Diallo", gender="F", date_of_birth=date(2014, 3, 1), nationality="Guinéenne"),
        class_a,
        enrollment_date=date(2026, 9, 2),
    )
    enrollment_services.enroll(
        make_student("Moussa", "Condé", gender="M", date_of_birth=date(2013, 11, 20)),
        class_b,
        enrollment_date=date(2026, 9, 10),
        kind="re_enrolment",
    )
    register = ClassRegister.objects.create(school=school, class_group=class_a, date=date(2026, 9, 15))
    AttendanceRecord.objects.create(school=school, register=register, enrollment=awa, status="absent")
    StaffAttendance.objects.create(school=school, staff=barry, date=date(2026, 9, 15), status="late")
    return {"a": class_a, "director": client_for(make_member(school, "director"), school)}


def report(client, key, **params):
    response = client.get(f"{URL}{key}/", params)
    assert response.status_code == 200, response.data
    return response.data


def test_enrolment_by_class_and_list(data):
    sections = report(data["director"], "enrollment")["sections"]
    by_class, listing = sections
    assert [(r["class"], r["new"], r["re_enrolment"], r["girls"], r["boys"]) for r in by_class["rows"]] == [
        ("7ème A", 1, 0, 1, 0),
        ("7ème B", 0, 1, 0, 1),
    ]
    assert by_class["totals"]["total"] == 2
    assert [r["student"] for r in listing["rows"]] == ["Awa Diallo", "Moussa Condé"]
    since = report(data["director"], "enrollment", date_from="2026-09-05")["sections"][1]["rows"]
    assert [r["student"] for r in since] == ["Moussa Condé"]


def test_classes_with_their_teachers(data):
    by_class, courses = report(data["director"], "classes")["sections"]
    a = by_class["rows"][0]
    assert (a["class"], a["class_teacher"], a["students"], a["fill"], a["subjects"]) == (
        "7ème A",
        "Mamadou Barry",
        1,
        "2.5",
        1,
    )
    assert courses["rows"] == [
        {"class": "7ème A", "subject": "Mathématiques", "teacher": "Mamadou Barry", "coefficient": "4.0"}
    ]


def test_teachers_and_their_load(data):
    staff, load = report(data["director"], "teachers")["sections"]
    assert staff["rows"][0]["homeroom"] == "7ème A" and staff["rows"][0]["access"] == "Non"
    assert (load["rows"][0]["classes"], load["rows"][0]["courses"]) == (1, 1)


def test_attendance_by_class_students_and_staff(data):
    by_class, students, staff = report(
        data["director"], "attendance", date_from="2026-09-01", date_to="2026-09-30"
    )["sections"]
    a = by_class["rows"][0]
    assert (a["registers"], a["absent"], a["rate"]) == (1, 1, "0.0")
    assert students["rows"][0]["student"] == "Awa Diallo"
    assert staff["rows"][0]["late"] == 1


def test_student_statistics(data, school, make_member, client_for):
    user = make_member(school, "director", email="en@test.local")
    user.language = "en"
    user.save()
    levels, ages, nations, kinds = report(client_for(user, school), "statistics")["sections"]
    assert levels["rows"][0]["students"] == 2 and levels["totals"]["girls"] == 1
    assert {r["nationality"] for r in nations["rows"]} == {"Guinéenne", "Not recorded"}
    assert {r["kind"] for r in kinds["rows"]} == {"New enrolment", "Re-enrolment"}


def test_access_and_exports(data, school, make_member, client_for):
    teacher = client_for(make_member(school, "teacher"), school)
    assert teacher.get(f"{URL}classes/").status_code == 403
    secretary = client_for(make_member(school, "admin_staff"), school)
    assert secretary.get(f"{URL}classes/").status_code == 200
    assert secretary.get(f"{URL}classes/", {"export": "pdf"}).status_code == 403  # no reports.export
    pdf = data["director"].get(f"{URL}enrollment/", {"export": "pdf"})
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")
    assert AuditLog.objects.filter(module="reports", entity_id="enrollment").exists()
    assert data["director"].get(f"{URL}nothing/").status_code == 404
    bad = data["director"].get(f"{URL}attendance/", {"date_from": "2026-10-01", "date_to": "2026-09-01"})
    assert bad.status_code == 400
