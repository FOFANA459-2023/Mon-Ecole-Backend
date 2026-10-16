"""The words of every notification and email, in French and English. Each entry is (title, body) and is
filled with str.format(**params); the recipient's language picks the version."""

MESSAGES: dict[str, dict[str, tuple[str, str]]] = {
    "announcement": {
        "fr": ("{title}", "{excerpt}"),
        "en": ("{title}", "{excerpt}"),
    },
    "grades_submitted": {
        "fr": ("Notes à valider : {subject} — {class_name}", "{teacher} a soumis les notes de {term}."),
        "en": ("Marks to review: {subject} — {class_name}", "{teacher} submitted the marks of {term}."),
    },
    "grades_sent_back": {
        "fr": ("Notes renvoyées : {subject} — {class_name}", "{term} : {reason}"),
        "en": ("Marks sent back: {subject} — {class_name}", "{term}: {reason}"),
    },
    "grades_reopened": {
        "fr": ("Notes rouvertes : {subject} — {class_name}", "{term} : {reason}"),
        "en": ("Marks reopened: {subject} — {class_name}", "{term}: {reason}"),
    },
    "grades_published": {
        "fr": (
            "Notes publiées : {subject} — {class_name}",
            "Les notes de {term} comptent désormais dans les résultats.",
        ),
        "en": ("Marks published: {subject} — {class_name}", "The marks of {term} now count in the results."),
    },
    "absence_alert": {
        "fr": (
            "{count} élève(s) trop souvent absent(s)",
            "Nouvelles alertes d'absentéisme à suivre.",
        ),
        "en": (
            "{count} student(s) absent too often",
            "New absenteeism alerts to follow up.",
        ),
    },
    "payments_overdue": {
        "fr": ("{count} facture(s) passée(s) en retard", "Échéances non réglées depuis hier."),
        "en": ("{count} invoice(s) became overdue", "Installments left unpaid since yesterday."),
    },
    "exam_soon": {
        "fr": ("Examen le {date} : {title}", "{levels}"),
        "en": ("Exam on {date}: {title}", "{levels}"),
    },
    # Emails to parents (when the school turns them on).
    "parent_payment_due": {
        "fr": (
            "{school} — échéance du {date}",
            "Bonjour,\n\nNous vous rappelons que {amount} sont à régler pour {student} "
            "au plus tard le {date} (facture {invoice}).\n\nMerci de votre confiance.\n{school}",
        ),
        "en": (
            "{school} — payment due on {date}",
            "Hello,\n\nThis is a reminder that {amount} is due for {student} by {date} "
            "(invoice {invoice}).\n\nThank you.\n{school}",
        ),
    },
    "parent_payment_overdue": {
        "fr": (
            "{school} — paiement en retard",
            "Bonjour,\n\nL'échéance du {date} pour {student} n'est pas réglée : {amount} restent dus "
            "(facture {invoice}). Merci de passer à la caisse ou de nous contacter.\n\n{school}",
        ),
        "en": (
            "{school} — payment overdue",
            "Hello,\n\nThe installment due on {date} for {student} has not been paid: {amount} is still due "
            "(invoice {invoice}). Please come to the cash desk or contact us.\n\n{school}",
        ),
    },
    "parent_absence": {
        "fr": (
            "{school} — absence de {student}",
            "Bonjour,\n\n{student} ({class_name}) était absent(e) le {date}. "
            "Merci de nous en indiquer le motif.\n\n{school}",
        ),
        "en": (
            "{school} — {student} was absent",
            "Hello,\n\n{student} ({class_name}) was absent on {date}. "
            "Please let us know the reason.\n\n{school}",
        ),
    },
}


def render(kind: str, language: str, **params) -> tuple[str, str]:
    texts = MESSAGES[kind]
    title, body = texts.get(language, texts["fr"])
    return title.format(**params)[:200], body.format(**params)
