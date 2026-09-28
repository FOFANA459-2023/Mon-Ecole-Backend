from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.files import SchoolUploadPath
from apps.core.models import TenantScopedModel


class Gender(models.TextChoices):
    MALE = "M", _("Male")
    FEMALE = "F", _("Female")


class Person(TenantScopedModel):
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    gender = models.CharField(max_length=1, choices=Gender.choices, blank=True)
    date_of_birth = models.DateField(null=True, blank=True)
    phone = models.CharField(max_length=30, blank=True)
    email = models.EmailField(blank=True)
    address = models.TextField(blank=True)

    class Meta:
        abstract = True

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()

    def __str__(self):
        return self.full_name


class Student(Person):
    class Status(models.TextChoices):
        ACTIVE = "active", _("Active")
        ARCHIVED = "archived", _("Archived")

    student_number = models.CharField(max_length=30)
    place_of_birth = models.CharField(max_length=100, blank=True)
    nationality = models.CharField(max_length=60, blank=True)
    photo = models.FileField(upload_to=SchoolUploadPath("photos/students"), blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.ACTIVE)
    archived_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["last_name", "first_name"]
        verbose_name = "student"
        constraints = [
            models.UniqueConstraint(fields=["school", "student_number"], name="uniq_student_number"),
        ]
        indexes = [
            models.Index(fields=["school", "last_name", "first_name"], name="student_school_name_idx"),
            models.Index(fields=["school", "status"], name="student_school_status_idx"),
        ]


class Guardian(TenantScopedModel):
    """A parent or guardian. One guardian can be linked to several students (siblings)."""

    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    phone = models.CharField(max_length=30, blank=True)
    alt_phone = models.CharField(max_length=30, blank=True)
    email = models.EmailField(blank=True)
    address = models.TextField(blank=True)
    occupation = models.CharField(max_length=100, blank=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="guardian_profiles",
    )

    class Meta:
        ordering = ["last_name", "first_name"]
        verbose_name = "guardian"
        indexes = [models.Index(fields=["school", "phone"], name="guardian_school_phone_idx")]

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()

    def __str__(self):
        return self.full_name


class StudentGuardian(TenantScopedModel):
    class Relationship(models.TextChoices):
        FATHER = "father", _("Father")
        MOTHER = "mother", _("Mother")
        GUARDIAN = "guardian", _("Guardian")
        OTHER = "other", _("Other")

    student = models.ForeignKey(Student, on_delete=models.CASCADE, related_name="guardian_links")
    guardian = models.ForeignKey(Guardian, on_delete=models.CASCADE, related_name="student_links")
    relationship = models.CharField(
        max_length=10, choices=Relationship.choices, default=Relationship.GUARDIAN
    )
    is_primary = models.BooleanField(default=False, help_text="Main contact for the school.")
    is_financial_contact = models.BooleanField(
        default=False, help_text="Receives invoices and payment reminders."
    )

    class Meta:
        ordering = ["-is_primary", "relationship"]
        verbose_name = "student guardian"
        constraints = [models.UniqueConstraint(fields=["student", "guardian"], name="uniq_student_guardian")]

    def __str__(self):
        return f"{self.guardian} → {self.student} ({self.relationship})"


class StaffMember(Person):
    class StaffType(models.TextChoices):
        TEACHER = "teacher", _("Teacher")
        ADMINISTRATIVE = "administrative", _("Administrative")
        SUPPORT = "support", _("Support")

    class Status(models.TextChoices):
        ACTIVE = "active", _("Active")
        ARCHIVED = "archived", _("Archived")

    employee_number = models.CharField(max_length=30)
    staff_type = models.CharField(max_length=20, choices=StaffType.choices, default=StaffType.TEACHER)
    position = models.CharField(max_length=100, blank=True, help_text="Job title, e.g. Mathematics teacher.")
    qualification = models.CharField(max_length=150, blank=True)
    specialization = models.CharField(max_length=150, blank=True)
    employment_date = models.DateField(null=True, blank=True)
    photo = models.FileField(upload_to=SchoolUploadPath("photos/staff"), blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.ACTIVE)
    archived_at = models.DateTimeField(null=True, blank=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="staff_profiles",
        help_text="Login account, when this person uses Mon École.",
    )

    class Meta:
        ordering = ["last_name", "first_name"]
        verbose_name = "staff member"
        constraints = [
            models.UniqueConstraint(fields=["school", "employee_number"], name="uniq_employee_number"),
            models.UniqueConstraint(
                fields=["school", "user"],
                condition=models.Q(user__isnull=False),
                name="uniq_staff_user_per_school",
            ),
        ]
        indexes = [models.Index(fields=["school", "status", "staff_type"], name="staff_school_status_idx")]
