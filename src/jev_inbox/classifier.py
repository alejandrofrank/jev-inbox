"""Conservative, inspectable English/Spanish rules. No external model or mail upload."""

import re
from dataclasses import dataclass

# Broad candidate search; every candidate still passes the conservative classifier.
JOB_SEARCH_QUERY = (
    "{application applying job jobs career hiring recruitment candidate position "
    'employment candidatura puesto empleo entrevista "oportunidad laboral"}'
)


@dataclass(frozen=True)
class Decision:
    status: str
    reason: str
    application: bool = True

    @property
    def labels(self):
        suffix = {"job_alert": "JobAlert", "opportunity": "Opportunity"}.get(
            self.status, self.status.title()
        )
        return ("JEV", f"JEV/{suffix}")


def matches(pattern: str, text: str) -> bool:
    return re.search(pattern, text, re.IGNORECASE) is not None


def classify(subject: str, body: str) -> Decision | None:
    # Old quoted replies must not override a new application update.
    body = re.split(
        r"(?im)^\s*(?:>|on .+wrote:|el .+escribi[oó]:|--+\s*original message)", body, maxsplit=1
    )[0]
    text = f"{subject}\n{body[:16000]}"
    if matches(
        r"\b(loan|mortgage|credit card|visa|scholarship|rental application|"
        r"software application)\b",
        subject,
    ) and not matches(r"\b(job|position|employment|candidate|candidatura|puesto)\b", text):
        return None
    if matches(
        r"\b(job alerts?|jobs for you|new jobs matching|recommended jobs|"
        r"alertas? de empleo|ofertas de empleo para ti)\b",
        text,
    ):
        return Decision("job_alert", "job-alert phrase", False)

    context = matches(
        r"\b(your application|application for|job application|applying for|"
        r"position|candidate|hiring|recruitment|job interview|employment|"
        r"candidatura|proceso de selecci[oó]n|puesto|entrevista de trabajo)\b",
        text,
    )
    rules = (
        (
            "rejected",
            r"not (?:be )?(?:moving|proceeding) forward|"
            r"(?:will not|won't|unable to) (?:be )?(?:proceed|move forward)|"
            r"(?:other|another) candidate|application (?:was |has been )?(?:rejected|unsuccessful)|"
            r"no (?:has sido|ha sido) seleccionad[oa]|no continuaremos",
        ),
        (
            "offer",
            r"(?:job|employment|offer of employment) offer|offer of employment|"
            r"pleased to offer you|oferta (?:de trabajo|laboral)|ofrecerte el puesto",
        ),
        (
            "interview",
            r"(?:schedule|invite|invitation|inviting|confirm|upcoming).{0,60}interview|"
            r"interview.{0,60}(?:schedule|invitation|availability|confirmed)|"
            r"(?:invita|programa|confirm).{0,60}entrevista",
        ),
        (
            "assessment",
            r"(?:complete|take|invited|next step).{0,60}(?:assessment|coding challenge|"
            r"technical test)|(?:prueba t[eé]cnica|evaluaci[oó]n t[eé]cnica)",
        ),
        (
            "applied",
            r"(?:thank you|thanks) for (?:applying|your application)|"
            r"(?:received|receiving) your application|application (?:received|confirmation)|"
            r"gracias por (?:postular|presentar tu candidatura)|hemos recibido tu candidatura",
        ),
    )
    # Prefer fresh subject evidence over body content. Generic status words alone do not match.
    short_acknowledgment = matches(
        r"^(?:(?:thank you|thanks) for applying[.!]?|"
        r"application (?:received|confirmation))$",
        subject.strip(),
    )
    for evidence in (subject, text):
        for status, pattern in rules:
            if matches(pattern, evidence) and (context or short_acknowledgment):
                return Decision(status, f"{status} phrase with application context")
    if matches(r"\b(career opportunity|hiring for|recruiting for|oportunidad laboral)\b", text):
        return Decision("opportunity", "recruiting phrase", False)
    if context and matches(
        r"update (?:on|regarding) your application|"
        r"actualizaci[oó]n de tu candidatura",
        text,
    ):
        return Decision("update", "application update phrase")
    return None
