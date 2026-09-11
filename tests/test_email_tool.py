from datetime import date

from calendarmate.tools.email_tool import Email, emails_in_range, load_emails, needs_attention_pool, unread_emails


def _email(
    sender: str, subject: str, received_at: str, read: bool = False, replied: bool = False
) -> Email:
    return Email(
        sender=sender,
        subject=subject,
        body="...",
        read=read,
        received_at=date.fromisoformat(received_at),
        replied=replied,
    )


def test_load_emails_reads_the_fixture() -> None:
    emails = load_emails()
    # 10 original entries + 1 read-but-recent, genuinely action-required
    # entry ("Henry") + 2 unread entries (a real bill and a bank
    # marketing email) added for Payment/Deadline Reminder coverage.
    assert len(emails) == 13
    assert any(e.subject == "Need your approval on the Q3 budget" for e in emails)


def test_unread_emails_excludes_read_ones() -> None:
    emails = [
        _email("A", "Read one", "2025-06-10", read=True),
        _email("B", "Unread one", "2025-06-10", read=False),
    ]
    result = unread_emails(emails)
    assert [e.subject for e in result] == ["Unread one"]


def test_fixture_has_two_read_emails() -> None:
    emails = load_emails()
    unread = unread_emails(emails)
    assert len(unread) == 11
    assert all(e.sender != "Grace" for e in unread)  # Grace's is read
    assert all("Henry" not in e.sender for e in unread)  # Henry's is read


def test_emails_in_range_includes_read_and_unread() -> None:
    emails = [
        _email("A", "In range, unread", "2025-06-10", read=False),
        _email("B", "In range, read", "2025-06-11", read=True),
        _email("C", "Out of range", "2025-06-20", read=False),
    ]
    result = emails_in_range(emails, date(2025, 6, 9), date(2025, 6, 15))
    assert [e.subject for e in result] == ["In range, unread", "In range, read"]


def test_emails_in_range_is_inclusive_and_sorted() -> None:
    emails = [
        _email("A", "Later", "2025-06-13"),
        _email("B", "Earlier", "2025-06-12"),
        _email("C", "Boundary start", "2025-06-09"),
        _email("D", "Boundary end", "2025-06-15"),
    ]
    result = emails_in_range(emails, date(2025, 6, 9), date(2025, 6, 15))
    assert [e.subject for e in result] == ["Boundary start", "Earlier", "Later", "Boundary end"]


def test_needs_attention_pool_includes_unread_and_recently_read() -> None:
    # A real recruiter email that had already been read (and was
    # therefore invisible to a plain unread-only "needs attention" check)
    # still genuinely needed a reply -- opening an email doesn't resolve
    # whether the person who sent it is still waiting on the user.
    today = date(2025, 6, 17)
    emails = [
        _email("A", "Unread, today", "2025-06-17", read=False),
        _email("B", "Read, today", "2025-06-17", read=True),
        _email("C", "Read, 2 days ago", "2025-06-15", read=True),
        _email("D", "Read, a week ago", "2025-06-10", read=True),
        _email("E", "Unread, a week ago", "2025-06-10", read=False),
    ]
    result = needs_attention_pool(emails, today)
    subjects = {e.subject for e in result}
    assert subjects == {"Unread, today", "Read, today", "Read, 2 days ago", "Unread, a week ago"}
    assert "Read, a week ago" not in subjects


def test_needs_attention_pool_excludes_an_email_already_replied_to() -> None:
    # A real recruiter email stayed flagged as needing attention even
    # after the user had actually replied, since read/unread alone can't
    # tell "opened it" apart from "opened it and replied." `replied`
    # must exclude an email outright, regardless of how recently it was
    # read -- a real reply already sent is the clearest possible signal.
    today = date(2025, 6, 17)
    emails = [
        _email("A", "Read today, not yet replied", "2025-06-17", read=True, replied=False),
        _email("B", "Read today, already replied", "2025-06-17", read=True, replied=True),
    ]
    result = needs_attention_pool(emails, today)
    subjects = {e.subject for e in result}
    assert subjects == {"Read today, not yet replied"}


def test_needs_attention_pool_on_the_real_fixture_includes_henrys_read_email() -> None:
    emails = load_emails()
    pool = needs_attention_pool(emails, date(2025, 6, 17))
    senders = {e.sender for e in pool}
    assert "Henry (Acme Recruiting)" in senders
    assert "Grace" not in senders  # read, but from over a week ago -- excluded


def test_fixture_last_week_slice_has_the_expected_three_emails() -> None:
    # Mirrors the calendar fixture's week (Mon 2025-06-16 - Sun 2025-06-22):
    # "last week" relative to that is Mon 2025-06-09 - Sun 2025-06-15.
    emails = load_emails()
    last_week = emails_in_range(emails, date(2025, 6, 9), date(2025, 6, 15))
    senders = {e.sender for e in last_week}
    assert senders == {"IT Security", "Frank", "Grace"}
    assert any(e.read for e in last_week)  # Grace's read email must still show up
