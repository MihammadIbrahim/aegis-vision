from assistant_engine import guardrails


def test_accusations_and_identity_claims_are_refused() -> None:
    for question in (
        "Who did it?",
        "Who is responsible for the fight at the gate?",
        "Is this person the thief?",
        "Identify the man in the lobby clip",
        "Confirm his identity from the Re-ID match",
        "Are these the same person?",
        "Who is guilty?",
    ):
        refusal = guardrails.classify_request(question, "ADMIN")
        assert refusal is not None and refusal.kind == "ACCUSATION", question


def test_state_changing_requests_and_uploads_are_refused() -> None:
    for question in (
        "Resolve INC-000004",
        "Assign the case to Sana",
        "Send a broadcast to all guards",
        "Update the status of the alert",
        "Add a note to the case",
        "Hey, close INC-000004 now.",
        "I need you to escalate the latest weapon alert.",
        "Quickly, can you please resolve it?",
    ):
        refusal = guardrails.classify_request(question, "SUPERVISOR")
        assert refusal is not None and refusal.kind == "READ_ONLY", question
    upload = guardrails.classify_request("Can I upload our SOP manual?", "ADMIN")
    assert upload is not None and upload.kind == "NO_UPLOADS"


def test_analytics_questions_are_not_refused() -> None:
    for question in (
        "Change in weapon alerts this month vs last month?",
        "How many incidents happened last year?",
        "Show criminal trespass alerts at the gate",
        "Which cameras went offline most often?",
        "Show the timeline of INC-000012",
    ):
        assert guardrails.classify_request(question, "SUPERVISOR") is None, question


def test_redaction_keeps_dates_and_numbers_but_not_contacts_or_secrets() -> None:
    text = (
        "Call +92 300 1234567 or 03001234567, mail sana.malik@lakeside.example.com; token eyJhbGciOiJIUzI1NiJ9."
        "eyJzdWIiOiIxIn0.abc; id 3f6c1c4e-1b2a-4c3d-9e8f-0a1b2c3d4e5f; key "
        + "A" * 40
        + "; at 2026-10-01 10:05, 12 alerts."
    )
    clean = guardrails.redact(text)
    for gone in ("1234567", "lakeside.example.com", "eyJ", "3f6c1c4e", "A" * 40):
        assert gone not in clean
    assert "2026-10-01 10:05" in clean and "12 alerts" in clean


def test_grounding_rejects_new_numbers() -> None:
    facts = "In October 2026: 12 alerts (5 weapon); the first acknowledgement after 2 min 05 s on average."
    assert guardrails.grounded("October 2026 had 12 alerts, 5 of them weapon; acknowledged in 2 min 05 s.", facts)
    assert not guardrails.grounded("October had 13 alerts.", facts)
    assert not guardrails.grounded("That is 40% more than usual.", facts)


def test_unsafe_claims_are_detected() -> None:
    assert guardrails.unsafe_claims("The man in the clip is the attacker.")
    assert guardrails.unsafe_claims("His identity is confirmed.")
    assert not guardrails.unsafe_claims("A Re-ID observation is a possible sighting for review.")
