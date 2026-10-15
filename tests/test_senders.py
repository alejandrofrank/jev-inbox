import pytest

from jev_inbox.senders import sender_context


@pytest.mark.parametrize(
    "local,domain,role",
    [
        ("person", "internal.statusneo.com", "recruiter"),
        ("person", "globalm.io", "recruiter"),
        ("person", "jobgether.com", "job_platform"),
        ("hit-reply", "linkedin.com", "recruiter_reply_relay"),
        ("jobalerts-noreply", "linkedin.com", "job_alert_sender"),
        ("jobs-noreply", "linkedin.com", "job_platform"),
        ("noreply", "glassdoor.com", "job_platform"),
        ("careers-noreply", "google.com", "unknown"),
        ("person", "statusneo.com.example.test", "unknown"),
        ("person", "notstatusneo.com", "unknown"),
    ],
)
def test_sender_context_uses_domain_boundaries_and_distinguishes_shared_senders(
    local, domain, role
):
    header = f"Synthetic Person <{local}@{domain}>"
    assert sender_context(header) == (domain, role)


def test_invalid_sender_does_not_gain_recruiter_context():
    assert sender_context("Not an address") == ("", "unknown")
