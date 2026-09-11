from calendarmate.tools.followup_tool import find_meeting, load_meetings, send_followup_email


def test_load_meetings_reads_the_fixture() -> None:
    meetings = load_meetings()
    assert len(meetings) == 4
    assert any(m.meeting_id == "m_001" for m in meetings)


def test_find_meeting_by_exact_id() -> None:
    meetings = load_meetings()
    result = find_meeting(meetings, meeting_id="m_002")
    assert result is not None
    assert result.title == "1:1 with Jordan"


def test_find_meeting_by_title_hint_is_case_insensitive_and_partial() -> None:
    meetings = load_meetings()
    result = find_meeting(meetings, title_hint="design review")
    assert result is not None
    assert result.meeting_id == "m_004"


def test_find_meeting_returns_none_when_nothing_matches() -> None:
    meetings = load_meetings()
    assert find_meeting(meetings, meeting_id="does-not-exist") is None
    assert find_meeting(meetings, title_hint="quarterly all hands") is None


def test_send_followup_email_records_and_returns_a_message_id() -> None:
    sent: list[dict] = []
    record = send_followup_email(
        sent, "Follow-up: Q3 Roadmap Sync", "Action items: ...", ["Priya", "Marcus", "Alice"]
    )
    assert sent == [record]
    assert record["status"] == "sent"
    assert record["message_id"].startswith("mock-")
    assert record["recipients"] == ["Priya", "Marcus", "Alice"]
