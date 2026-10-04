import os
import tempfile
import shutil
import pytest
from email.message import EmailMessage

from core.email_archive import EmailArchive, decode_mime_header, html_to_plain_text


@pytest.fixture
def temp_archive():
    temp_dir = tempfile.mkdtemp(prefix="test_email_archive_")
    db_path = os.path.join(temp_dir, "test_archive.db")
    archive = EmailArchive(db_path=db_path)
    yield archive
    shutil.rmtree(temp_dir, ignore_errors=True)


def test_mime_decode_and_html_cleaning():
    assert decode_mime_header("=?utf-8?b?SGVsbG8gV29ybGQ=?=") == "Hello World"
    assert decode_mime_header("Plain Text") == "Plain Text"
    assert decode_mime_header(None) == ""

    html = "<html><body><h1>Hello</h1><p>This is a <b>test</b>.</p><br/>Line 2</body></html>"
    text = html_to_plain_text(html)
    assert "Hello" in text
    assert "This is a test." in text
    assert "Line 2" in text


def test_save_and_retrieve_plain_email(temp_archive):
    msg = EmailMessage()
    msg["Subject"] = "Welcome to Open Amity"
    msg["From"] = "system@example.com"
    msg["To"] = "agent@example.com"
    msg["Date"] = "Wed, 01 Oct 2026 12:00:00 +0000"
    msg["Message-ID"] = "<msg-101@example.com>"
    msg.set_content("Hello Agent, this is your first email.")

    archive_id = temp_archive.save_email(folder="INBOX", msg_obj=msg, server_uid="1001", is_read=False)
    assert archive_id == 1

    emails = temp_archive.get_emails(folder="INBOX", limit=10, unread_only=False)
    assert len(emails) == 1
    assert emails[0]["id"] == 1
    assert emails[0]["subject"] == "Welcome to Open Amity"
    assert emails[0]["from_sender"] == "system@example.com"
    assert emails[0]["is_read"] == 0

    # Retrieve full email
    full = temp_archive.get_email("1")
    assert full is not None
    assert full["subject"] == "Welcome to Open Amity"
    assert "Hello Agent" in full["body_plain"]


def test_deduplication(temp_archive):
    msg = EmailMessage()
    msg["Subject"] = "Important Alert"
    msg["From"] = "security@example.com"
    msg["To"] = "agent@example.com"
    msg["Message-ID"] = "<alert-202@example.com>"
    msg.set_content("Critical notice.")

    id1 = temp_archive.save_email(folder="INBOX", msg_obj=msg, server_uid="50", is_read=False)
    id2 = temp_archive.save_email(folder="INBOX", msg_obj=msg, server_uid="50", is_read=True)

    assert id1 == id2
    emails = temp_archive.get_emails(folder="INBOX")
    assert len(emails) == 1
    # Read status should be updated
    assert emails[0]["is_read"] == 1


def test_multipart_email_with_attachments(temp_archive):
    msg = EmailMessage()
    msg["Subject"] = "Monthly Report with PDF"
    msg["From"] = "boss@example.com"
    msg["To"] = "agent@example.com"
    msg["Message-ID"] = "<rep-303@example.com>"
    msg.set_content("Please find attached report.")
    msg.add_alternative("<p>Please find attached report in HTML.</p>", subtype="html")
    msg.add_attachment(b"%PDF-1.4 fake pdf data", maintype="application", subtype="pdf", filename="report.pdf")

    archive_id = temp_archive.save_email(folder="INBOX", msg_obj=msg, server_uid="88")
    assert archive_id > 0

    full = temp_archive.get_email(str(archive_id))
    assert full["has_attachments"] == 1
    assert "report.pdf" in full["attachment_names"]
    assert "Please find attached report." in full["body_plain"]


def test_unread_filtering_and_mark_as_read(temp_archive):
    msg1 = EmailMessage()
    msg1["Subject"] = "Email One"
    msg1["Message-ID"] = "<id-1@example.com>"
    msg1.set_content("Content 1")

    msg2 = EmailMessage()
    msg2["Subject"] = "Email Two"
    msg2["Message-ID"] = "<id-2@example.com>"
    msg2.set_content("Content 2")

    temp_archive.save_email(folder="INBOX", msg_obj=msg1, is_read=False)
    temp_archive.save_email(folder="INBOX", msg_obj=msg2, is_read=False)

    unread = temp_archive.get_emails(folder="INBOX", unread_only=True)
    assert len(unread) == 2

    # Mark first as read
    assert temp_archive.mark_as_read("1", is_read=True) is True

    unread = temp_archive.get_emails(folder="INBOX", unread_only=True)
    assert len(unread) == 1
    assert unread[0]["id"] == 2

    # Mark back as unread
    assert temp_archive.mark_as_read("1", is_read=False) is True
    assert len(temp_archive.get_emails(folder="INBOX", unread_only=True)) == 2


def test_search_emails(temp_archive):
    msg1 = EmailMessage()
    msg1["Subject"] = "Invoice from ACME Corp"
    msg1["From"] = "billing@acme.com"
    msg1["To"] = "agent@example.com"
    msg1["Message-ID"] = "<inv-1@acme.com>"
    msg1["Date"] = "Mon, 01 Sep 2026 10:00:00 +0000"
    msg1.set_content("Total due: $500 for consulting services.")

    msg2 = EmailMessage()
    msg2["Subject"] = "Coffee meetup"
    msg2["From"] = "friend@example.com"
    msg2["To"] = "agent@example.com"
    msg2["Message-ID"] = "<coffee-2@example.com>"
    msg2["Date"] = "Wed, 30 Sep 2026 15:00:00 +0000"
    msg2.set_content("Are you free for coffee tomorrow?")

    temp_archive.save_email(folder="INBOX", msg_obj=msg1)
    temp_archive.save_email(folder="INBOX", msg_obj=msg2)

    # Search keyword
    res = temp_archive.search_emails(folder="INBOX", query="consulting")
    assert len(res) == 1
    assert res[0]["subject"] == "Invoice from ACME Corp"

    # Search sender
    res = temp_archive.search_emails(folder="INBOX", sender="friend")
    assert len(res) == 1
    assert res[0]["subject"] == "Coffee meetup"

    # Search subject
    res = temp_archive.search_emails(folder="INBOX", subject="Invoice")
    assert len(res) == 1


def test_delete_and_list_folders(temp_archive):
    msg = EmailMessage()
    msg["Subject"] = "Trash email"
    msg["Message-ID"] = "<trash-1@example.com>"
    msg.set_content("Delete me")

    temp_archive.save_email(folder="Trash", msg_obj=msg)
    folders = temp_archive.list_folders()
    assert "Trash" in folders
    assert "INBOX" in folders

    assert temp_archive.delete_email("1", folder="Trash") is True
    assert temp_archive.get_email("1") is None


def test_stats(temp_archive):
    msg1 = EmailMessage()
    msg1["Subject"] = "Read msg"
    msg1["Message-ID"] = "<m1@test.org>"
    msg1.set_content("A")

    msg2 = EmailMessage()
    msg2["Subject"] = "Unread msg"
    msg2["Message-ID"] = "<m2@test.org>"
    msg2.set_content("B")

    temp_archive.save_email(folder="INBOX", msg_obj=msg1, is_read=True)
    temp_archive.save_email(folder="INBOX", msg_obj=msg2, is_read=False)

    stats = temp_archive.get_stats()
    assert stats["total"] == 2
    assert stats["unread"] == 1
