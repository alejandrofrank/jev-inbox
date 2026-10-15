import copy
import json

import httpx
import pytest

from jev_inbox.classifier import CRITERIA, ENDPOINT, MODEL, JevClassifier, JevError, parse_response


def response_data(category="interview", p=0.97, confidence=0.96, relevance=None, alert=None):
    others = [name for name in CRITERIA if name != category]
    probabilities = {name: (1 - p) / len(others) for name in others}
    probabilities[category] = p
    return {
        "model": MODEL,
        "answers": {
            "correspondence": {
                "type": "noul",
                "noul": (0.01 if category in {"unrelated", "job_alert"} else 0.99)
                if relevance is None
                else relevance,
            },
            "job_alert": {
                "type": "noul",
                "noul": (0.99 if category == "job_alert" else 0.01) if alert is None else alert,
            },
            "category": {
                "type": "choice",
                "choice": category,
                "confidence": confidence,
                "probabilities": probabilities,
            },
        },
    }


def test_calls_official_jev_with_semantic_context_and_no_keyword_gate():
    captured = []

    def handler(request):
        captured.append(request)
        return httpx.Response(200, json=response_data())

    client = JevClassifier("synthetic", transport=httpx.MockTransport(handler))
    try:
        result = client("Next steps", "We would like you to meet the team on Tuesday.")
    finally:
        client.close()
    request = captured[0]
    assert str(request.url) == ENDPOINT and request.method == "POST"
    assert request.headers["Authorization"] == "Bearer synthetic"
    payload = json.loads(request.content)
    assert payload["model"] == MODEL
    assert payload["state"]["subject"] == "Next steps"
    assert set(payload["state"]) == {"subject", "body", "sender_domain", "sender_role"}
    assert payload["questions"]["category"]["type"] == "choice"
    assert result.decision.status == "interview"
    assert result.decision.labels == ("grok", "grok/Interview")


@pytest.mark.parametrize("category", list(CRITERIA))
def test_category_mapping_and_application_tracking(category):
    result = parse_response(response_data(category))
    if category in {"unrelated", "job_alert"}:
        assert result.outcome == "ignored" and result.decision is None
    else:
        assert result.decision.status == category
        assert result.decision.application == (category not in {"opportunity", "job_alert"})


@pytest.mark.parametrize("relevance,alert", [(0.6, 0.01), (0.95, 0.4), (0.5, 0.4)])
def test_uncertain_relevance_cannot_label_mail(relevance, alert):
    result = parse_response(response_data(relevance=relevance, alert=alert))
    assert result.outcome == "uncertain" and result.decision is None


def test_stage_ambiguity_does_not_hide_clear_recruiter_mail():
    result = parse_response(response_data(p=0.49, confidence=0.43))
    assert result.outcome == "matched"


@pytest.mark.parametrize("value", [True, None, "0.99", -0.1, 1.1, float("nan"), float("inf")])
def test_invalid_confidence_fails_closed(value):
    data = response_data()
    data["answers"]["category"]["confidence"] = value
    with pytest.raises(JevError):
        parse_response(data)


def test_invalid_response_shapes_never_create_decisions():
    good = response_data()
    invalid = [None, [], {}, {"model": "not-jev", "answers": good["answers"]}]
    for field, value in [
        ("type", "noul"),
        ("choice", "archive"),
        ("probabilities", {"interview": 1.0}),
        ("probabilities", dict.fromkeys(CRITERIA, 1.0)),
    ]:
        candidate = copy.deepcopy(good)
        candidate["answers"]["category"][field] = value
        invalid.append(candidate)
    for data in invalid:
        with pytest.raises(JevError):
            parse_response(data)


@pytest.mark.parametrize("status", [301, 307, 400, 401, 403, 422])
def test_rejected_requests_do_not_fallback_leak_or_follow_redirects(status):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(
            status,
            text="PRIVATE_RESPONSE_MARKER",
            headers={"Location": "https://example.test/collect"},
        )

    client = JevClassifier("synthetic", transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(JevError) as caught:
            client("Thank you for applying", "PRIVATE_BODY_MARKER")
        assert "PRIVATE" not in str(caught.value) and "synthetic" not in str(caught.value)
        assert len(calls) == 1
    finally:
        client.close()


def test_temporary_errors_retry_with_backoff_and_retry_after():
    statuses = iter([429, 529, 200])
    sleeps = []

    def handler(request):
        status = next(statuses)
        return httpx.Response(status, json=response_data(), headers={"Retry-After": "3"})

    client = JevClassifier("synthetic", transport=httpx.MockTransport(handler), sleep=sleeps.append)
    try:
        assert client("Next steps", "").outcome == "matched"
    finally:
        client.close()
    assert sleeps == [3, 3]


def test_retries_are_bounded():
    calls, sleeps = [], []

    def handler(request):
        calls.append(request)
        return httpx.Response(529)

    client = JevClassifier("synthetic", transport=httpx.MockTransport(handler), sleep=sleeps.append)
    try:
        with pytest.raises(JevError):
            client("Next steps", "")
    finally:
        client.close()
    assert len(calls) == 4 and sleeps == [1, 2, 4]


def test_payload_is_bounded():
    def handler(request):
        state = json.loads(request.content)["state"]
        assert len(state["subject"]) == 500 and len(state["body"]) == 16000
        return httpx.Response(200, json=response_data("unrelated"))

    client = JevClassifier("synthetic", transport=httpx.MockTransport(handler))
    try:
        assert client("s" * 1000, "b" * 40000).decision is None
    finally:
        client.close()


@pytest.mark.parametrize("name", ["correspondence", "job_alert"])
@pytest.mark.parametrize("value", [None, True, "0.99", -1, 2, float("nan")])
def test_invalid_relevance_answers_fail_closed(name, value):
    data = response_data()
    data["answers"][name]["noul"] = value
    with pytest.raises(JevError):
        parse_response(data)


def test_missing_relevance_answer_cannot_reuse_old_category_gate():
    data = response_data()
    del data["answers"]["correspondence"]
    with pytest.raises(JevError):
        parse_response(data)


def test_digest_is_excluded_even_when_stage_is_confident():
    assert parse_response(response_data(relevance=0.98, alert=0.95)).decision is None


def test_sender_context_is_evidence_and_dedicated_alert_sender_is_excluded():
    seen = []

    def handler(request):
        seen.append(json.loads(request.content)["state"])
        return httpx.Response(200, json=response_data(p=0.49, confidence=0.43))

    client = JevClassifier("synthetic", transport=httpx.MockTransport(handler))
    try:
        for role in ("job_platform", "recruiter_reply_relay", "job_alert_sender"):
            result = client(
                "Next steps",
                "Synthetic hiring message.",
                sender_domain="linkedin.com",
                sender_role=role,
            )
            assert (result.outcome == "matched") == (role != "job_alert_sender")
        assert seen[0]["sender_domain"] == "linkedin.com"
    finally:
        client.close()
