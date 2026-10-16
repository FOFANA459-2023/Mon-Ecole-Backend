"""Issued report cards: stored, reused while unchanged, versioned when something changes; bulk jobs."""

import io
import zipfile
from datetime import date, timedelta

import pytest
from django.utils import timezone
from pypdf import PdfReader

from apps.assessments.models import ReportCard, ReportComment
from apps.audit.models import AuditLog
from apps.core import jobs
from apps.core.models import Job

from .conftest import URL, gradebook_of, save

pytestmark = pytest.mark.django_db


@pytest.fixture
def published(setup, term, maths_marked, school, make_member, client_for):
    director = client_for(make_member(school, "director"), school)
    assert director.post(f"{URL}{maths_marked['gradebook'].pk}/publish/").status_code == 200
    return {"director": director, **maths_marked}


def pages(content: bytes) -> int:
    return len(PdfReader(io.BytesIO(content)).pages)


def print_class(client, class_group, term, **extra):
    response = client.get("/api/v1/report-cards/", {"class_group": class_group.pk, "term": term.pk, **extra})
    assert response.status_code == 200
    return response.content


def versions(enrollment, term):
    return list(
        ReportCard.objects.filter(enrollment=enrollment, term=term)
        .order_by("version")
        .values_list("version", flat=True)
    )


class TestIssuing:
    def test_printing_issues_each_card_once(self, setup, term, published):
        director = published["director"]
        first = print_class(director, setup["class"], term)
        assert pages(first) == 3
        assert ReportCard.objects.count() == 3
        awa = setup["enrollments"]["Awa"]
        card = ReportCard.objects.get(enrollment=awa, term=term)
        assert (card.version, card.rank, str(card.average)) == (1, 2, "15.170")

        # Nothing changed: the stored cards are handed out again.
        print_class(director, setup["class"], term)
        print_class(director, setup["class"], term, enrollment=awa.pk)
        assert ReportCard.objects.count() == 3

    def test_a_change_issues_a_new_version_and_keeps_the_old_one(self, setup, term, published, school):
        director = published["director"]
        print_class(director, setup["class"], term)
        e = setup["enrollments"]
        ReportComment.objects.create(school=school, enrollment=e["Binta"], term=term, comment="Des progrès.")
        print_class(director, setup["class"], term)
        assert versions(e["Binta"], term) == [1, 2]
        assert versions(e["Awa"], term) == [1]

        # Marks reopened, corrected and published again: every card of the class may change (ranks).
        gradebook = published["gradebook"]
        assert director.post(f"{URL}{gradebook.pk}/reopen/", {"reason": "Erreur"}).status_code == 200
        compo = published["items"]["Composition du 1er trimestre"]
        save(director, gradebook, [{"assessment": compo, "enrollment": e["Awa"].pk, "score": "40"}])
        assert director.post(f"{URL}{gradebook.pk}/publish/").status_code == 200
        print_class(director, setup["class"], term)
        assert versions(e["Awa"], term) == [1, 2]
        assert ReportCard.objects.get(enrollment=e["Awa"], term=term, version=2).rank == 1

    def test_the_year_card_is_kept_apart_from_term_cards(self, setup, term, published):
        director = published["director"]
        response = director.get("/api/v1/report-cards/", {"class_group": setup["class"].pk})
        assert response.status_code == 200
        assert ReportCard.objects.filter(term__isnull=True).count() == 3
        director.get("/api/v1/report-cards/", {"class_group": setup["class"].pk})
        assert ReportCard.objects.filter(term__isnull=True).count() == 3


class TestArchive:
    def test_a_student_s_issued_cards_with_every_version(self, setup, term, published, school):
        director = published["director"]
        e = setup["enrollments"]
        print_class(director, setup["class"], term)
        ReportComment.objects.create(school=school, enrollment=e["Awa"], term=term, comment="Bien.")
        print_class(director, setup["class"], term, enrollment=e["Awa"].pk)

        rows = director.get("/api/v1/issued-report-cards/", {"student": e["Awa"].student_id}).data
        assert [(r["version"], r["is_latest"], r["term_name"]) for r in rows] == [
            (2, True, term.name),
            (1, False, term.name),
        ]
        assert rows[0]["class_name"] == "7ème A" and rows[0]["academic_year_name"] == "2026-2027"
        response = director.get(f"/api/v1/issued-report-cards/{rows[1]['id']}/download/")
        assert response.status_code == 200 and response["Content-Type"] == "application/pdf"
        assert (
            "awa-diallo" in response["Content-Disposition"] and "-v1.pdf" in response["Content-Disposition"]
        )
        assert pages(response.content) == 1

    def test_teachers_see_only_the_cards_of_the_class_they_lead(
        self, setup, term, published, school, client_for, other_school, make_member
    ):
        print_class(published["director"], setup["class"], term)
        student = setup["enrollments"]["Awa"].student_id
        camara = client_for(setup["camara"], school)
        assert len(camara.get("/api/v1/issued-report-cards/", {"student": student}).data) == 1
        barry = client_for(setup["barry"], school)
        assert barry.get("/api/v1/issued-report-cards/", {"student": student}).data == []
        card = ReportCard.objects.first()
        assert barry.get(f"/api/v1/issued-report-cards/{card.pk}/download/").status_code == 404
        stranger = client_for(make_member(other_school, "director"), other_school)
        assert stranger.get(f"/api/v1/issued-report-cards/{card.pk}/download/").status_code == 404


class TestJobs:
    def start(self, client, payload, django_capture_on_commit_callbacks):
        with django_capture_on_commit_callbacks(execute=True):
            response = client.post("/api/v1/report-cards/jobs/", payload, format="json")
        assert response.status_code == 202, response.data
        return client.get(f"/api/v1/jobs/{response.data['id']}/").data

    def test_the_whole_school_in_the_background(
        self, setup, term, year, published, make_class, make_student, django_capture_on_commit_callbacks
    ):
        from apps.enrollments import services as enrollment_services

        other = make_class("7ème B")
        enrollment_services.enroll(make_student("Kadiatou", "Sylla"), other)
        director = published["director"]
        job = self.start(
            director, {"academic_year": year.pk, "term": term.pk}, django_capture_on_commit_callbacks
        )
        assert job["status"] == "done", job
        assert (job["total"], job["done"]) == (4, 4)
        assert job["outcome"] == {"classes": 2, "cards": 4, "new": 4}
        assert job["result_name"].endswith(".zip")
        download = director.get(f"/api/v1/jobs/{job['id']}/download/")
        assert download.status_code == 200
        archive = zipfile.ZipFile(io.BytesIO(b"".join(download.streaming_content)))
        names = sorted(archive.namelist())
        assert len(names) == 2 and all(n.endswith(".pdf") for n in names)
        assert sorted(pages(archive.read(n)) for n in names) == [1, 3]
        log = AuditLog.objects.get(module="grades", entity_type="report_cards")
        assert "2 classes" in log.summary

        # The same job again issues nothing new.
        again = self.start(
            director, {"academic_year": year.pk, "term": term.pk}, django_capture_on_commit_callbacks
        )
        assert again["outcome"]["new"] == 0

    def test_one_class_gives_one_pdf(self, setup, term, year, published, django_capture_on_commit_callbacks):
        job = self.start(
            published["director"],
            {"academic_year": year.pk, "term": term.pk, "class_group": setup["class"].pk},
            django_capture_on_commit_callbacks,
        )
        assert job["result_name"].endswith(".pdf") and job["outcome"]["cards"] == 3

    def test_who_may_start_one_and_see_it(
        self,
        setup,
        term,
        year,
        published,
        school,
        client_for,
        make_member,
        django_capture_on_commit_callbacks,
    ):
        camara = client_for(setup["camara"], school)
        payload = {"academic_year": year.pk, "term": term.pk}
        assert camara.post("/api/v1/report-cards/jobs/", payload, format="json").status_code == 403
        job = self.start(published["director"], payload, django_capture_on_commit_callbacks)
        # Another director of the same school does not see someone else's job.
        colleague = client_for(make_member(school, "director", email="second@test.local"), school)
        assert colleague.get(f"/api/v1/jobs/{job['id']}/").status_code == 404
        assert colleague.get(f"/api/v1/jobs/{job['id']}/download/").status_code == 404

    def test_a_term_of_another_year_is_refused(self, setup, term, school, published):
        from apps.academics.services import create_academic_year

        other_year = create_academic_year(
            school, name="2027-2028", start_date=date(2027, 9, 1), end_date=date(2028, 6, 30), term_count=3
        )
        response = published["director"].post(
            "/api/v1/report-cards/jobs/", {"academic_year": other_year.pk, "term": term.pk}, format="json"
        )
        assert response.status_code == 400 and "term" in response.data["fields"]

    def test_a_failing_job_says_so(
        self, setup, term, year, published, monkeypatch, django_capture_on_commit_callbacks
    ):
        def boom(job):
            raise RuntimeError("disk full")

        monkeypatch.setattr("apps.assessments.archive.run_report_cards_job", boom)
        job = self.start(
            published["director"],
            {"academic_year": year.pk, "term": term.pk},
            django_capture_on_commit_callbacks,
        )
        assert job["status"] == "failed" and job["error"] == jobs.FAILED_MESSAGE
        assert "disk full" not in job["error"]

    def test_old_result_files_are_deleted(
        self, setup, term, year, published, django_capture_on_commit_callbacks
    ):
        job = self.start(
            published["director"],
            {"academic_year": year.pk, "term": term.pk},
            django_capture_on_commit_callbacks,
        )
        assert jobs.purge_old_results() == 0
        assert jobs.purge_old_results(now=timezone.now() + timedelta(days=8)) == 1
        stored = Job.objects.get(pk=job["id"])
        assert not stored.result and stored.status == Job.Status.DONE
        assert published["director"].get(f"/api/v1/jobs/{job['id']}/download/").status_code == 404


def test_reopened_marks_leave_the_card_so_it_is_a_new_version(setup, term, published):
    """While maths is reopened the card has no published subject: a different card, so a new version."""
    director = published["director"]
    print_class(director, setup["class"], term)
    gradebook = gradebook_of(setup["maths"], term)
    director.post(f"{URL}{gradebook.pk}/reopen/", {"reason": "Vérification"})
    print_class(director, setup["class"], term)
    assert versions(setup["enrollments"]["Awa"], term) == [1, 2]
    assert (
        ReportCard.objects.get(enrollment=setup["enrollments"]["Awa"], term=term, version=2).average is None
    )
