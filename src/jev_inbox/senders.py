"""Sender evidence for Jev, not an allowlist that bypasses semantic classification."""

from email.utils import parseaddr

PLATFORMS = {
    "linkedin.com",
    "indeed.com",
    "glassdoor.com",
    "greenhouse.io",
    "lever.co",
    "myworkday.com",
    "workday.com",
    "smartrecruiters.com",
    "ashbyhq.com",
    "teamtailor-mail.com",
    "jobgether.com",
}
RECRUITERS = {"statusneo.com", "globalm.io", "global-m.com"}


def sender_context(header):
    """Never send personal mailbox names or display names to the classifier."""
    address = parseaddr(header)[1].casefold()
    local, separator, domain = address.rpartition("@")
    if not separator or not domain or len(domain) > 253:
        return "", "unknown"
    domain = domain.rstrip(".")
    if domain == "linkedin.com":
        if local == "jobalerts-noreply":
            return domain, "job_alert_sender"
        if local == "hit-reply":
            return domain, "recruiter_reply_relay"
        if local == "messages-noreply":
            return domain, "linkedin_message_sender"
        if local == "notifications-noreply":
            return domain, "linkedin_notification_sender"
    if any(domain == root or domain.endswith("." + root) for root in RECRUITERS):
        return domain, "recruiter"
    if any(domain == root or domain.endswith("." + root) for root in PLATFORMS):
        return domain, "job_platform"
    # Shared mailboxes (e.g. Glassdoor noreply / Google Careers) also carry genuine
    # application updates. Their message purpose must be judged by Jev.
    return domain, "unknown"
