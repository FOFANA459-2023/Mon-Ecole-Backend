import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from apps.documents.models import Document


@pytest.fixture(autouse=True)
def _media(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path


def pdf_file(name="acte.pdf"):
    return SimpleUploadedFile(name, b"%PDF-1.4 test", content_type="application/pdf")


@pytest.mark.django_db
class TestDocuments:
    def test_upload_list_download_delete(self, school, make_student, make_member, client_for):
        student = make_student()
        client = client_for(make_member(school, "admin_staff"), school)
        response = client.post(
            "/api/v1/documents/",
            {
                "owner_type": "student",
                "owner_id": student.pk,
                "category": "birth_certificate",
                "title": "Acte de naissance",
                "file": pdf_file(),
            },
            format="multipart",
        )
        assert response.status_code == 201, response.data
        doc_id = response.data["id"]

        listing = client.get("/api/v1/documents/", {"owner_type": "student", "owner_id": student.pk})
        assert [d["title"] for d in listing.data] == ["Acte de naissance"]

        download = client.get(f"/api/v1/documents/{doc_id}/download/")
        assert download.status_code == 200
        assert b"".join(download.streaming_content).startswith(b"%PDF")

        assert client.delete(f"/api/v1/documents/{doc_id}/").status_code == 204
        assert not Document.objects.exists()

    def test_executables_are_refused(self, school, make_student, make_member, client_for):
        student = make_student()
        bad = SimpleUploadedFile("virus.exe", b"MZ", content_type="application/x-msdownload")
        response = client_for(make_member(school, "admin_staff"), school).post(
            "/api/v1/documents/",
            {"owner_type": "student", "owner_id": student.pk, "file": bad},
            format="multipart",
        )
        assert response.status_code == 400

    def test_student_of_another_school_is_not_found(
        self, school, other_school, make_student, make_member, client_for
    ):
        foreign = make_student(target_school=other_school)
        response = client_for(make_member(school, "director"), school).post(
            "/api/v1/documents/",
            {"owner_type": "student", "owner_id": foreign.pk, "file": pdf_file()},
            format="multipart",
        )
        assert response.status_code == 404

    def test_read_only_roles_cannot_upload(self, school, make_student, make_member, client_for):
        student = make_student()
        response = client_for(make_member(school, "accountant"), school).post(
            "/api/v1/documents/",
            {"owner_type": "student", "owner_id": student.pk, "file": pdf_file()},
            format="multipart",
        )
        assert response.status_code == 403
