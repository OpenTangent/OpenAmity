import os
import shutil
import tempfile
from unittest.mock import MagicMock, patch
from email.message import EmailMessage
import pytest

from tools.email_tool import EmailTool
from core.email_archive import EmailArchive


@pytest.fixture
def email_tool_fixture():
    temp_dir = tempfile.mkdtemp(prefix="test_email_tool_")
    db_path = os.path.join(temp_dir, "email_archive.db")
    downloads_dir = os.path.join(temp_dir, "Downloads")
    os.makedirs(downloads_dir, exist_ok=True)

    tool = EmailTool()
    tool._archive = EmailArchive(agent_id="test_agent", db_path=db_path)

    # Mock settings manager
    mock_sm = MagicMock()
    stored_env = {
        "EMAIL_ADDRESS": "agent@example.com",
        "EMAIL_PASSWORD": "secretpassword",
        "EMAIL_IMAP_HOST": "imap.example.com",
        "EMAIL_IMAP_PORT": "993",
        "EMAIL_IMAP_SECURITY": "SSL/TLS",
        "EMAIL_SMTP_HOST": "smtp.example.com",
        "EMAIL_SMTP_PORT": "465",
        "EMAIL_SMTP_SECURITY": "SSL/TLS",
        "EMAIL_ARCHIVE_LOCAL": "true",
        "EMAIL_DISPLAY_NAME": "Test Agent",
        "EMAIL_USERNAME": "agent@example.com"
    }
    mock_sm.get_env.side_effect = lambda k, d="": stored_env.get(k, d)
    mock_sm.get.side_effect = lambda k, d=None: stored_env.get(k, d)

    mock_orch = MagicMock()
    mock_orch.settings_manager = mock_sm
    mock_orch.agent_id = "test_agent"
    tool.orchestrator = mock_orch

    with patch.object(tool, "_get_agent_download_dir", return_value=downloads_dir):
        yield tool, stored_env, downloads_dir

    shutil.rmtree(temp_dir, ignore_errors=True)


def create_sample_rfc822(subject="Test Email", body="Hello World", from_addr="sender@test.com", has_att=False):
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = "agent@example.com"
    msg["Date"] = "Wed, 01 Oct 2026 14:00:00 +0000"
    msg["Message-ID"] = f"<{abs(hash(subject))}@test.com>"
    msg.set_content(body)
    if has_att:
        msg.add_attachment(b"dummy pdf content", maintype="application", subtype="pdf", filename="invoice.pdf")
    return msg.as_bytes()


def test_fetch_emails_moves_to_archive_and_purges_server(email_tool_fixture):
    tool, env, _ = email_tool_fixture

    mock_imap = MagicMock()
    raw_email = create_sample_rfc822(subject="Urgent Task", body="Please review the document.")

    # Server has one email with UID 101
    mock_imap.select.return_value = ("OK", [b"1"])
    mock_imap.uid.side_effect = [
        # search:
        ("OK", [b"101"]),
        # fetch RFC822 FLAGS:
        ("OK", [(b'1 (FLAGS (\\Recent))', raw_email)]),
        # store \Deleted:
        ("OK", [b"101"]),
    ]
    mock_imap.expunge.return_value = ("OK", [b"101"])

    with patch.object(tool, "_get_imap_connection", return_value=(True, mock_imap)):
        res = tool.execute("fetch_emails", folder="INBOX", limit=10)

    # Verify IMAP operations
    mock_imap.select.assert_called_with('"INBOX"', readonly=False)
    # Check that it stored \Deleted and expunged
    mock_imap.uid.assert_any_call("store", b"101", "+FLAGS", "\\Deleted")
    mock_imap.expunge.assert_called_once()
    mock_imap.logout.assert_called_once()

    # Verify returned result includes the email
    assert "Found 1 email(s) in 'INBOX':" in res
    assert "Urgent Task" in res
    assert "[UNREAD]" in res

    # Verify email is now stored in local archive
    archived = tool.archive.get_emails(folder="INBOX")
    assert len(archived) == 1
    assert archived[0]["subject"] == "Urgent Task"


def test_fetch_emails_returns_local_archive_when_server_empty(email_tool_fixture):
    tool, env, _ = email_tool_fixture

    # Pre-populate archive with an email
    msg = EmailMessage()
    msg["Subject"] = "Existing Archived Email"
    msg["From"] = "friend@example.com"
    msg["To"] = "agent@example.com"
    msg.set_content("Archived content")
    tool.archive.save_email(folder="INBOX", msg_obj=msg, is_read=True)

    # Server search returns empty
    mock_imap = MagicMock()
    mock_imap.select.return_value = ("OK", [b"0"])
    mock_imap.uid.return_value = ("OK", [b""])

    with patch.object(tool, "_get_imap_connection", return_value=(True, mock_imap)):
        res = tool.execute("fetch_emails", folder="INBOX", limit=10, unread_only=False)

    assert "Found 1 email(s) in 'INBOX':" in res
    assert "Existing Archived Email" in res
    assert "[READ]" in res


def test_read_email_reads_from_local_archive(email_tool_fixture):
    tool, env, _ = email_tool_fixture

    # Pre-populate archive
    raw_email = create_sample_rfc822(subject="Quarterly Goals", body="Here are the Q4 goals.")
    arc_id = tool.archive.save_email(folder="INBOX", raw_bytes=raw_email, is_read=False)

    # read_email should read directly from local archive without needing IMAP
    res = tool.execute("read_email", email_id=str(arc_id), folder="INBOX", mark_as_read=True)

    assert f"=== Email Details (ID: {arc_id}) ===" in res
    assert "Quarterly Goals" in res
    assert "Here are the Q4 goals." in res

    # Verify marked as read in local archive
    em = tool.archive.get_email(str(arc_id))
    assert em["is_read"] == 1


def test_search_emails_searches_local_archive(email_tool_fixture):
    tool, env, _ = email_tool_fixture

    raw1 = create_sample_rfc822(subject="Budget Plan 2026", body="Detailed budget allocation.")
    raw2 = create_sample_rfc822(subject="Lunch invitation", body="Let's grab pizza.")
    tool.archive.save_email(folder="INBOX", raw_bytes=raw1)
    tool.archive.save_email(folder="INBOX", raw_bytes=raw2)

    # Server has no new emails
    mock_imap = MagicMock()
    mock_imap.select.return_value = ("OK", [b"0"])
    mock_imap.uid.return_value = ("OK", [b""])

    with patch.object(tool, "_get_imap_connection", return_value=(True, mock_imap)):
        res = tool.execute("search_emails", query="Budget", folder="INBOX")

    assert "Found 1 match(es)" in res
    assert "Budget Plan 2026" in res


def test_download_attachment_from_local_archive(email_tool_fixture):
    tool, env, downloads_dir = email_tool_fixture

    raw_att = create_sample_rfc822(subject="Invoice Attached", body="See attached.", has_att=True)
    arc_id = tool.archive.save_email(folder="INBOX", raw_bytes=raw_att)

    res = tool.execute("download_attachment", email_id=str(arc_id), attachment_filename="invoice.pdf", folder="INBOX")

    assert "Attachment successfully downloaded and saved" in res
    expected_file = os.path.join(downloads_dir, "invoice.pdf")
    assert os.path.exists(expected_file)
    with open(expected_file, "rb") as f:
        assert f.read() == b"dummy pdf content"


def test_delete_email_removes_from_local_archive(email_tool_fixture):
    tool, env, _ = email_tool_fixture

    raw = create_sample_rfc822(subject="Spam Notice", body="Buy now!")
    arc_id = tool.archive.save_email(folder="INBOX", raw_bytes=raw)

    mock_imap = MagicMock()
    with patch.object(tool, "_get_imap_connection", return_value=(True, mock_imap)):
        res = tool.execute("delete_email", email_id=str(arc_id), folder="INBOX")

    assert f"Email '{arc_id}' deleted successfully from folder 'INBOX'." in res
    assert tool.archive.get_email(str(arc_id)) is None


def test_send_email_archives_to_sent_folder(email_tool_fixture):
    tool, env, _ = email_tool_fixture

    mock_smtp = MagicMock()
    with patch.object(tool, "_get_smtp_connection", return_value=(True, mock_smtp)):
        res = tool.execute(
            "send_email",
            to="recipient@example.com",
            subject="Hello there",
            body="This is a test outgoing email."
        )

    assert "Email successfully sent to 'recipient@example.com'" in res
    mock_smtp.send_message.assert_called_once()

    # Check that sent email was saved to local archive under 'Sent'
    sent_emails = tool.archive.get_emails(folder="Sent")
    assert len(sent_emails) == 1
    assert sent_emails[0]["subject"] == "Hello there"
    assert sent_emails[0]["is_read"] == 1


def test_sync_emails_explicit_command(email_tool_fixture):
    tool, env, _ = email_tool_fixture

    mock_imap = MagicMock()
    raw = create_sample_rfc822(subject="Sync Test", body="Sync message.")
    mock_imap.select.return_value = ("OK", [b"1"])
    mock_imap.uid.side_effect = [
        ("OK", [b"505"]),
        ("OK", [(b'1 (FLAGS ())', raw)]),
        ("OK", [b"505"])
    ]
    mock_imap.expunge.return_value = ("OK", [b"505"])

    with patch.object(tool, "_get_imap_connection", return_value=(True, mock_imap)):
        res = tool.execute("sync_emails", folder="INBOX")

    assert "IMAP sync complete: Successfully moved 1 email(s) to local archive." in res
    mock_imap.expunge.assert_called_once()
    assert len(tool.archive.get_emails(folder="INBOX")) == 1


def test_legacy_direct_imap_when_archive_local_disabled(email_tool_fixture):
    tool, env, _ = email_tool_fixture
    env["EMAIL_ARCHIVE_LOCAL"] = "false"

    mock_imap = MagicMock()
    mock_imap.select.return_value = ("OK", [b"1"])
    mock_imap.uid.side_effect = [
        ("OK", [b"999"]),
        ("OK", [(b'1 (FLAGS (\\Seen))', b"From: direct@test.com\r\nSubject: Direct IMAP\r\n\r\nDirect")])
    ]

    with patch.object(tool, "_get_imap_connection", return_value=(True, mock_imap)):
        res = tool.execute("fetch_emails", folder="INBOX")

    assert "Direct IMAP" in res
    # Should NOT have called expunge or store \Deleted
    mock_imap.expunge.assert_not_called()
    assert len(tool.archive.get_emails(folder="INBOX")) == 0


def test_get_imap_connection_retries_on_socket_eof(email_tool_fixture):
    tool, env, _ = email_tool_fixture
    import imaplib

    mock_imap1 = MagicMock()
    mock_imap1.login.side_effect = imaplib.IMAP4.abort("command: LOGIN => socket error: EOF")
    mock_imap2 = MagicMock()
    mock_imap2.login.return_value = ("OK", [b"Logged in"])

    with patch("imaplib.IMAP4_SSL", side_effect=[mock_imap1, mock_imap2]), \
         patch("time.sleep", return_value=None) as mock_sleep:
        success, res = tool._get_imap_connection()

    assert success is True
    assert res == mock_imap2
    mock_sleep.assert_called_once_with(1.0)
    mock_imap1.shutdown.assert_called_once()


def test_fetch_emails_surfaces_sync_failure_when_no_unread_in_archive(email_tool_fixture):
    tool, env, _ = email_tool_fixture

    # Pre-populate archive with an existing read email
    raw = create_sample_rfc822(subject="Old Read Email", body="Read body.")
    tool.archive.save_email(folder="INBOX", raw_bytes=raw, is_read=True)

    # Server sync fails with IMAP connection abort
    err_msg = "IMAP connection or authentication error: abort: command: LOGIN => socket error: EOF"
    with patch.object(tool, "_get_imap_connection", return_value=(False, err_msg)):
        res = tool.execute("fetch_emails", folder="INBOX", unread_only=True)

    # Must NOT swallow the error and say "No unread emails found"
    assert "Failed to connect to IMAP server:" in res
    assert "socket error: EOF" in res
    assert "No unread emails found" not in res


def test_fetch_emails_shows_notice_when_sync_fails_but_cached_unread_exist(email_tool_fixture):
    tool, env, _ = email_tool_fixture

    # Pre-populate archive with an unread email
    raw = create_sample_rfc822(subject="Cached Unread Email", body="Unread body.")
    tool.archive.save_email(folder="INBOX", raw_bytes=raw, is_read=False)

    err_msg = "IMAP connection or authentication error: socket.timeout: timed out"
    with patch.object(tool, "_get_imap_connection", return_value=(False, err_msg)):
        res = tool.execute("fetch_emails", folder="INBOX", unread_only=True)

    assert "[Notice: IMAP server sync failed" in res
    assert "Found 1 unread email(s) in 'INBOX':" in res
    assert "Cached Unread Email" in res


def test_read_email_surfaces_sync_error_on_missing_email(email_tool_fixture):
    tool, env, _ = email_tool_fixture

    err_msg = "IMAP connection or authentication error: abort: command: LOGIN => socket error: EOF"
    with patch.object(tool, "_get_imap_connection", return_value=(False, err_msg)):
        res = tool.execute("read_email", email_id="9999", folder="INBOX")

    assert "IMAP server sync failed" in res
    assert "socket error: EOF" in res

