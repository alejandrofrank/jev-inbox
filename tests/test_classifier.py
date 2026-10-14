import pytest

from jev_inbox.classifier import classify


@pytest.mark.parametrize(
    "subject,body,status",
    [
        ("Thank you for applying", "", "applied"),
        ("Your application", "We are not moving forward with your application.", "rejected"),
        ("Your application: interview invitation", "", "interview"),
        ("Your application", "Please complete the coding challenge.", "assessment"),
        ("Your application", "We are pleased to offer you the position.", "offer"),
        ("Hemos recibido tu candidatura", "", "applied"),
        ("Tu candidatura", "No has sido seleccionado para el puesto.", "rejected"),
        ("Career opportunity", "We are hiring for a developer role.", "opportunity"),
        ("Job alert", "New jobs matching your preferences", "job_alert"),
        ("Update on your application", "", "update"),
    ],
)
def test_explicit_job_evidence(subject, body, status):
    assert classify(subject, body).status == status


@pytest.mark.parametrize(
    "subject,body",
    [
        ("Dinner tomorrow", "See you then"),
        ("Special offer", "Get 20% off"),
        ("Software application update", "Installation completed"),
        ("Interview with an author", "Read our newsletter"),
        ("Credit application", "Your loan has been approved"),
        ("Assessment results", "Your health assessment is ready"),
        ("Hello", "Thanks!\n> Thank you for applying for the position"),
        ("Thanks for applying the configuration", "The software works now"),
        ("Loan application received", "Thank you for your application"),
    ],
)
def test_unrelated_mail_is_untouched(subject, body):
    assert classify(subject, body) is None


def test_new_subject_wins_over_old_body_status():
    decision = classify(
        "Your application: interview invitation", "Thank you for applying for the position."
    )
    assert decision.status == "interview"
