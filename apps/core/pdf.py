"""Shared helpers for the PDFs the school prints (cards, forms, later receipts and report cards).

ReportLab is pure Python, so the same code runs on developer machines and on the ARM servers.
"""

from django.http import HttpResponse
from reportlab.lib.colors import HexColor
from reportlab.lib.utils import ImageReader

NAVY = HexColor("#1F3A5F")
TEAL = HexColor("#2E7D6B")
LIGHT = HexColor("#EEF3F8")
MUTED = HexColor("#5A6675")

LABELS = {
    "fr": {
        "student_card": "CARTE SCOLAIRE",
        "student_number": "Matricule",
        "class": "Classe",
        "year": "Année scolaire",
        "born": "Né(e) le",
        "enrolment_form": "FICHE D'INSCRIPTION",
        "student": "Élève",
        "last_name": "Nom",
        "first_name": "Prénom(s)",
        "gender": "Sexe",
        "date_of_birth": "Date de naissance",
        "place_of_birth": "Lieu de naissance",
        "nationality": "Nationalité",
        "address": "Adresse",
        "phone": "Téléphone",
        "email": "E-mail",
        "schooling": "Scolarité",
        "level": "Niveau",
        "enrollment_date": "Date d'inscription",
        "kind": "Type d'inscription",
        "previous_school": "Établissement précédent",
        "guardians": "Parents / tuteurs",
        "relationship": "Lien",
        "name": "Nom",
        "occupation": "Profession",
        "documents": "Pièces fournies",
        "none": "Aucune",
        "signature_guardian": "Signature du parent / tuteur",
        "signature_school": "Cachet et signature de l'établissement",
        "done_on": "Fait le",
        "M": "Masculin",
        "F": "Féminin",
        "father": "Père",
        "mother": "Mère",
        "guardian": "Tuteur",
        "other": "Autre",
        "new": "Nouvelle inscription",
        "re_enrolment": "Réinscription",
        "transfer_in": "Transfert",
    },
    "en": {
        "student_card": "STUDENT ID CARD",
        "student_number": "Student no.",
        "class": "Class",
        "year": "School year",
        "born": "Born",
        "enrolment_form": "ENROLMENT FORM",
        "student": "Student",
        "last_name": "Last name",
        "first_name": "First name(s)",
        "gender": "Gender",
        "date_of_birth": "Date of birth",
        "place_of_birth": "Place of birth",
        "nationality": "Nationality",
        "address": "Address",
        "phone": "Phone",
        "email": "Email",
        "schooling": "Schooling",
        "level": "Level",
        "enrollment_date": "Enrolment date",
        "kind": "Enrolment type",
        "previous_school": "Previous school",
        "guardians": "Parents / guardians",
        "relationship": "Relationship",
        "name": "Name",
        "occupation": "Occupation",
        "documents": "Documents provided",
        "none": "None",
        "signature_guardian": "Parent / guardian signature",
        "signature_school": "School stamp and signature",
        "done_on": "Date",
        "M": "Male",
        "F": "Female",
        "father": "Father",
        "mother": "Mother",
        "guardian": "Guardian",
        "other": "Other",
        "new": "New enrolment",
        "re_enrolment": "Re-enrolment",
        "transfer_in": "Transfer",
    },
}


def labels_for(school) -> dict[str, str]:
    return LABELS.get(school.default_language, LABELS["fr"])


def format_date(value, school) -> str:
    if not value:
        return ""
    return value.strftime("%d/%m/%Y" if school.default_language == "fr" else "%d %b %Y")


def image_reader(file_field) -> ImageReader | None:
    """Open an uploaded image for drawing; SVG and unreadable files are skipped."""
    if not file_field or str(file_field.name).lower().endswith(".svg"):
        return None
    try:
        file_field.open("rb")
        try:
            return ImageReader(file_field)
        finally:
            file_field.close()
    except Exception:
        return None


def pdf_response(content: bytes, filename: str, *, inline: bool = True) -> HttpResponse:
    response = HttpResponse(content, content_type="application/pdf")
    disposition = "inline" if inline else "attachment"
    response["Content-Disposition"] = f'{disposition}; filename="{filename}"'
    return response
