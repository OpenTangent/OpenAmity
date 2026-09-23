import os
import re
import ssl
import time
import email
import base64
import smtplib
import imaplib
import logging
import mimetypes
from email.message import EmailMessage
from email.header import decode_header
from email import policy
from typing import List, Dict, Any, Optional, Tuple

from core.cerebrum import Tool
from core.email_auth import OAuth2TokenManager, build_xoauth2_string

logger = logging.getLogger("tool.Email")


def _decode_mime_header(header_value: Optional[str]) -> str:
    """Decodes MIME encoded header strings to standard Python unicode string."""
    if not header_value:
        return ""
    decoded_fragments = []
    for fragment, encoding in decode_header(header_value):
        if isinstance(fragment, bytes):
            try:
                decoded_fragments.append(fragment.decode(encoding or "utf-8", errors="replace"))
            except Exception:
                decoded_fragments.append(fragment.decode("utf-8", errors="replace"))
        else:
            decoded_fragments.append(str(fragment))
    return "".join(decoded_fragments)


def _html_to_plain_text(html_content: str) -> str:
    """Converts HTML email body into clean, readable plain text."""
    if not html_content:
        return ""
    text = re.sub(r'<head.*?>.*?</head>', '', html_content, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<style.*?>.*?</style>', '', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<script.*?>.*?</script>', '', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<(?:br|p|div|tr|h[1-6])[\s/>]', '\n', text, flags=re.IGNORECASE)
    text = re.sub(r'<li[\s>]', '\n* ', text, flags=re.IGNORECASE)
    text = re.sub(r'<[^>]+>', '', text)
    text = re.sub(r'&nbsp;', ' ', text)
    text = re.sub(r'&amp;', '&', text)
    text = re.sub(r'&lt;', '<', text)
    text = re.sub(r'&gt;', '>', text)
    text = re.sub(r'&quot;', '"', text)
    text = re.sub(r'&#39;', "'", text)
    lines = [line.strip() for line in text.splitlines()]
    return "\n".join(chunk for chunk in lines if chunk)


MAX_ATTACHMENT_SIZE_BYTES = 25 * 1024 * 1024  # 25 MB max total attachment size (Gmail standard)


def _resolve_attachment_path(raw_path: str, agent_name: str = "Agent") -> Optional[str]:
    """Resolves an attachment path or filename across standard agent locations."""
    if not raw_path:
        return None

    raw_path = str(raw_path).strip()
    expanded = os.path.expanduser(raw_path)
    if os.path.isfile(expanded):
        return os.path.abspath(expanded)

    # Search in agent standard folders
    candidate_dirs = [
        os.path.expanduser(f"~/Downloads/{agent_name}"),
        os.path.expanduser(f"~/Documents/{agent_name}"),
        os.path.expanduser(f"~/Pictures/{agent_name}"),
        os.path.expanduser(f"~/Documents/{agent_name}/.scratch"),
        os.path.expanduser("~/Downloads"),
        os.path.expanduser("~/Documents"),
        os.path.expanduser("~"),
        os.getcwd()
    ]

    filename = os.path.basename(expanded)
    for cdir in candidate_dirs:
        candidate_path = os.path.join(cdir, filename)
        if os.path.isfile(candidate_path):
            return os.path.abspath(candidate_path)

    return None


class EmailTool(Tool):
    name = "Email"
    icon = "✉️"
    color = "#4169E1"
    async_commands = []
    description = "Manage the agent's dedicated email account: send emails, fetch recent messages, read full emails, search mailboxes, download attachments, and organize folders."
    commands = [
        "send_email",
        "fetch_emails",
        "read_email",
        "search_emails",
        "download_attachment",
        "mark_as_read",
        "delete_email",
        "list_folders"
    ]

    def __init__(self, orchestrator=None):
        super().__init__(orchestrator)
        self._token_manager = OAuth2TokenManager()

    def _get_settings(self) -> Dict[str, Any]:
        """Loads live email configuration from SettingsManager and environment."""
        sm = self.orchestrator.settings_manager if (self.orchestrator and hasattr(self.orchestrator, 'settings_manager')) else None

        def get_val(env_key: str, default: str = "") -> str:
            if sm and hasattr(sm, 'get_env'):
                return sm.get_env(env_key, default)
            return os.getenv(env_key, default)

        auth_type = get_val("EMAIL_AUTH_TYPE", "password").lower()
        email_address = get_val("EMAIL_ADDRESS", "")
        password = get_val("EMAIL_PASSWORD", "")
        oauth_client_id = get_val("EMAIL_OAUTH_CLIENT_ID", "")
        oauth_client_secret = get_val("EMAIL_OAUTH_CLIENT_SECRET", "")
        oauth_refresh_token = get_val("EMAIL_OAUTH_REFRESH_TOKEN", "")

        imap_host = get_val("EMAIL_IMAP_HOST", "")
        imap_port = int(get_val("EMAIL_IMAP_PORT", "993") or 993)
        imap_security = get_val("EMAIL_IMAP_SECURITY", "SSL/TLS").upper()

        smtp_host = get_val("EMAIL_SMTP_HOST", "")
        smtp_port = int(get_val("EMAIL_SMTP_PORT", "465") or 465)
        smtp_security = get_val("EMAIL_SMTP_SECURITY", "SSL/TLS").upper()

        agent_name = "Agent"
        if sm and hasattr(sm, 'get'):
            agent_name = sm.get("core.agent.name", "Agent")

        display_name = get_val("EMAIL_DISPLAY_NAME", "") or agent_name
        username = get_val("EMAIL_USERNAME", "") or email_address

        return {
            "auth_type": auth_type,
            "email_address": email_address,
            "password": password,
            "oauth_client_id": oauth_client_id,
            "oauth_client_secret": oauth_client_secret,
            "oauth_refresh_token": oauth_refresh_token,
            "imap_host": imap_host,
            "imap_port": imap_port,
            "imap_security": imap_security,
            "smtp_host": smtp_host,
            "smtp_port": smtp_port,
            "smtp_security": smtp_security,
            "display_name": display_name,
            "username": username,
            "agent_name": agent_name
        }

    def _get_imap_connection(self) -> Tuple[bool, Any]:
        """Establishes an authenticated IMAP client session."""
        cfg = self._get_settings()
        if not cfg["imap_host"]:
            logger.error("IMAP connection aborted: EMAIL_IMAP_HOST is not configured.")
            return False, "IMAP server host is not configured. Please set EMAIL_IMAP_HOST in settings."
        if not cfg["email_address"]:
            logger.error("IMAP connection aborted: EMAIL_ADDRESS is not configured.")
            return False, "Email address is not configured. Please set EMAIL_ADDRESS in settings."

        logger.debug(f"Connecting to IMAP server {cfg['imap_host']}:{cfg['imap_port']} (Security: {cfg['imap_security']}, Auth: {cfg['auth_type']})")
        try:
            if "SSL" in cfg["imap_security"] or cfg["imap_port"] == 993:
                context = ssl.create_default_context()
                imap = imaplib.IMAP4_SSL(cfg["imap_host"], cfg["imap_port"], ssl_context=context)
            else:
                imap = imaplib.IMAP4(cfg["imap_host"], cfg["imap_port"])
                if "STARTTLS" in cfg["imap_security"]:
                    imap.starttls(ssl_context=ssl.create_default_context())

            # Authentication
            if cfg["auth_type"] == "oauth2":
                logger.debug(f"Retrieving OAuth2 access token for IMAP user: {cfg['email_address']}...")
                success, token_or_err = self._token_manager.get_access_token(
                    cfg["oauth_client_id"],
                    cfg["oauth_client_secret"],
                    cfg["oauth_refresh_token"]
                )
                if not success:
                    imap.logout()
                    logger.error(f"IMAP OAuth2 token refresh failed: {token_or_err}")
                    return False, f"IMAP OAuth2 Authentication error: {token_or_err}"

                auth_str = build_xoauth2_string(cfg["email_address"], token_or_err, as_base64=False)
                logger.debug(f"Authenticating IMAP via XOAUTH2...")
                res, data = imap.authenticate("XOAUTH2", lambda x: auth_str)
                logger.debug(f"IMAP XOAUTH2 response: res={res}, data={data}")
                if res != "OK":
                    imap.logout()
                    logger.error(f"IMAP XOAUTH2 authentication rejected: {data}")
                    return False, f"IMAP XOAUTH2 authentication rejected: {data}"
                logger.info(f"IMAP authenticated successfully via XOAUTH2 for {cfg['email_address']}")
            else:
                if not cfg["password"]:
                    imap.logout()
                    logger.error("IMAP connection aborted: EMAIL_PASSWORD is not configured.")
                    return False, "Email password is not configured. Please set EMAIL_PASSWORD in settings."
                logger.debug(f"Authenticating IMAP via password for user: {cfg['username']}...")
                imap.login(cfg["username"], cfg["password"])
                logger.info(f"IMAP authenticated successfully via password for {cfg['username']}")

            return True, imap

        except Exception as e:
            logger.error(f"IMAP connection failed: {type(e).__name__}: {str(e)}", exc_info=True)
            return False, f"IMAP connection or authentication error: {type(e).__name__}: {str(e)}"

    def _get_smtp_connection(self) -> Tuple[bool, Any]:
        """Establishes an authenticated SMTP client session with proper EHLO sequencing."""
        cfg = self._get_settings()
        if not cfg["smtp_host"]:
            logger.error("SMTP connection aborted: EMAIL_SMTP_HOST is not configured.")
            return False, "SMTP server host is not configured. Please set EMAIL_SMTP_HOST in settings."
        if not cfg["email_address"]:
            logger.error("SMTP connection aborted: EMAIL_ADDRESS is not configured.")
            return False, "Email address is not configured. Please set EMAIL_ADDRESS in settings."

        logger.debug(f"Connecting to SMTP server {cfg['smtp_host']}:{cfg['smtp_port']} (Security: {cfg['smtp_security']}, Auth: {cfg['auth_type']})")
        try:
            if "SSL" in cfg["smtp_security"] or cfg["smtp_port"] == 465:
                context = ssl.create_default_context()
                smtp = smtplib.SMTP_SSL(cfg["smtp_host"], cfg["smtp_port"], context=context, timeout=20)
                # CRITICAL: Always issue EHLO after establishing SSL connection
                code, resp = smtp.ehlo()
                logger.debug(f"SMTP_SSL initial EHLO response: {code} {resp}")
            else:
                smtp = smtplib.SMTP(cfg["smtp_host"], cfg["smtp_port"], timeout=20)
                code, resp = smtp.ehlo()
                logger.debug(f"SMTP initial EHLO response: {code} {resp}")
                if "STARTTLS" in cfg["smtp_security"] or smtp.has_extn("STARTTLS"):
                    logger.debug("Upgrading SMTP connection via STARTTLS...")
                    smtp.starttls(context=ssl.create_default_context())
                    code, resp = smtp.ehlo()
                    logger.debug(f"SMTP post-STARTTLS EHLO response: {code} {resp}")

            # Ensure EHLO is completed and server extensions are populated
            smtp.ehlo_or_helo_if_needed()

            # Authentication
            if cfg["auth_type"] == "oauth2":
                logger.debug(f"Retrieving OAuth2 access token for SMTP user: {cfg['email_address']}...")
                success, token_or_err = self._token_manager.get_access_token(
                    cfg["oauth_client_id"],
                    cfg["oauth_client_secret"],
                    cfg["oauth_refresh_token"]
                )
                if not success:
                    smtp.quit()
                    logger.error(f"SMTP OAuth2 token refresh failed: {token_or_err}")
                    return False, f"SMTP OAuth2 Authentication error: {token_or_err}"

                auth_b64 = build_xoauth2_string(cfg["email_address"], token_or_err, as_base64=True)
                logger.debug(f"Sending SMTP AUTH XOAUTH2 command for {cfg['email_address']}...")
                code, resp = smtp.docmd("AUTH", f"XOAUTH2 {auth_b64}")
                logger.debug(f"SMTP AUTH response: code={code}, resp={resp}")

                if code == 334:
                    # Server sent challenge containing error details in base64
                    try:
                        err_json = base64.b64decode(resp).decode("utf-8", errors="replace")
                        logger.error(f"SMTP XOAUTH2 challenge error details: {err_json}")
                    except Exception:
                        pass
                    # Complete failure handshake with empty response
                    code, resp = smtp.docmd("")
                    logger.debug(f"SMTP AUTH challenge acknowledgement: code={code}, resp={resp}")

                if code != 235:
                    smtp.quit()
                    resp_text = resp.decode('utf-8', errors='replace') if isinstance(resp, bytes) else str(resp)
                    logger.error(f"SMTP XOAUTH2 authentication rejected ({code}): {resp_text}")
                    return False, f"SMTP XOAUTH2 authentication rejected ({code}): {resp_text}"

                logger.info(f"SMTP authenticated successfully via XOAUTH2 for {cfg['email_address']}")
            else:
                if not cfg["password"]:
                    smtp.quit()
                    logger.error("SMTP connection aborted: EMAIL_PASSWORD is not configured.")
                    return False, "Email password is not configured. Please set EMAIL_PASSWORD in settings."
                logger.debug(f"Authenticating SMTP via password for user: {cfg['username']}...")
                smtp.login(cfg["username"], cfg["password"])
                logger.info(f"SMTP authenticated successfully via password for {cfg['username']}")

            return True, smtp

        except Exception as e:
            logger.error(f"SMTP connection failed: {type(e).__name__}: {str(e)}", exc_info=True)
            return False, f"SMTP connection or authentication error: {type(e).__name__}: {str(e)}"

    def _get_agent_download_dir(self) -> str:
        """Determines the standard downloads path for the agent."""
        cfg = self._get_settings()
        agent_name = cfg.get("agent_name", "Agent")
        download_dir = os.path.expanduser(f"~/Downloads/{agent_name}")
        os.makedirs(download_dir, exist_ok=True)
        return download_dir

    def execute(self, command: str, *args, **kwargs) -> str:
        logger.debug(f"Executing EmailTool command '{command}' with args: {kwargs}")
        if command == "send_email":
            return self._execute_send_email(kwargs)
        elif command == "fetch_emails":
            return self._execute_fetch_emails(kwargs)
        elif command == "read_email":
            return self._execute_read_email(kwargs)
        elif command == "search_emails":
            return self._execute_search_emails(kwargs)
        elif command == "download_attachment":
            return self._execute_download_attachment(kwargs)
        elif command == "mark_as_read":
            return self._execute_mark_as_read(kwargs)
        elif command == "delete_email":
            return self._execute_delete_email(kwargs)
        elif command == "list_folders":
            return self._execute_list_folders(kwargs)
        else:
            err = f"Error: Unknown command '{command}' for EmailTool."
            logger.error(err)
            return err

    def _execute_send_email(self, params: dict) -> str:
        to_addr = params.get("to")
        subject = params.get("subject", "(No Subject)")
        body = params.get("body", "")
        html_body = params.get("html_body")
        cc = params.get("cc")
        bcc = params.get("bcc")
        reply_to = params.get("reply_to")
        in_reply_to_id = params.get("in_reply_to_message_id")
        attachments = params.get("attachments", [])

        if not to_addr:
            logger.error("send_email failed: 'to' recipient address is missing.")
            return "Error: 'to' recipient address is required to send an email."

        cfg = self._get_settings()
        from_header = f"{cfg['display_name']} <{cfg['email_address']}>" if cfg['display_name'] else cfg['email_address']

        msg = EmailMessage()
        msg["From"] = from_header
        
        # Handle multiple recipients
        if isinstance(to_addr, list):
            msg["To"] = ", ".join(to_addr)
            recipients = list(to_addr)
        else:
            msg["To"] = str(to_addr)
            recipients = [addr.strip() for addr in str(to_addr).split(",") if addr.strip()]

        if cc:
            if isinstance(cc, list):
                msg["Cc"] = ", ".join(cc)
                recipients.extend(cc)
            else:
                msg["Cc"] = str(cc)
                recipients.extend([addr.strip() for addr in str(cc).split(",") if addr.strip()])

        if bcc:
            if isinstance(bcc, list):
                recipients.extend(bcc)
            else:
                recipients.extend([addr.strip() for addr in str(bcc).split(",") if addr.strip()])

        msg["Subject"] = subject
        if reply_to:
            msg["Reply-To"] = reply_to
        if in_reply_to_id:
            msg["In-Reply-To"] = in_reply_to_id
            msg["References"] = in_reply_to_id

        # Set content
        msg.set_content(body)
        if html_body:
            msg.add_alternative(html_body, subtype="html")

        # Process attachments
        if attachments:
            if isinstance(attachments, str):
                attachments = [attachments]

            total_attachments_size = 0
            resolved_attachments = []

            # Pre-validate existence and size limits
            for raw_path in attachments:
                resolved_path = _resolve_attachment_path(raw_path, cfg.get("agent_name", "Agent"))
                if not resolved_path:
                    logger.error(f"send_email failed: attachment file not found for '{raw_path}'")
                    return f"Error: Attachment file '{raw_path}' not found. Please provide an absolute path or save it in your agent directory (~/Downloads/{cfg.get('agent_name', 'Agent')}/)."

                file_size = os.path.getsize(resolved_path)
                if file_size > MAX_ATTACHMENT_SIZE_BYTES:
                    err = f"Error: Attachment '{os.path.basename(resolved_path)}' ({file_size / (1024*1024):.2f} MB) exceeds the maximum allowed limit of 25 MB."
                    logger.error(f"send_email failed: {err}")
                    return err

                total_attachments_size += file_size
                if total_attachments_size > MAX_ATTACHMENT_SIZE_BYTES:
                    err = f"Error: Total attachments size ({total_attachments_size / (1024*1024):.2f} MB) exceeds the maximum allowed limit of 25 MB."
                    logger.error(f"send_email failed: {err}")
                    return err

                resolved_attachments.append((resolved_path, file_size))

            # Attach files to message
            for file_path, file_size in resolved_attachments:
                ctype, encoding = mimetypes.guess_type(file_path)
                if ctype is None or encoding is not None:
                    ctype = "application/octet-stream"
                maintype, subtype = ctype.split("/", 1)

                try:
                    with open(file_path, "rb") as f:
                        file_data = f.read()
                    filename = os.path.basename(file_path)
                    msg.add_attachment(file_data, maintype=maintype, subtype=subtype, filename=filename)
                    logger.debug(f"Attached file '{filename}' ({file_size} bytes, {ctype})")
                except Exception as e:
                    logger.error(f"Error attaching file '{file_path}': {e}", exc_info=True)
                    return f"Error attaching file '{file_path}': {str(e)}"

        logger.info(f"Sending email from '{from_header}' to '{msg['To']}', Subject: '{subject}', Attachments: {len(attachments)}")

        # Connect and send
        success, smtp_or_err = self._get_smtp_connection()
        if not success:
            logger.error(f"send_email SMTP connection failed: {smtp_or_err}")
            return f"Failed to connect to SMTP server: {smtp_or_err}"

        smtp = smtp_or_err
        try:
            smtp.send_message(msg, from_addr=cfg["email_address"], to_addrs=recipients)
            smtp.quit()
            att_info = f" with {len(attachments)} attachment(s)" if attachments else ""
            success_msg = f"Email successfully sent to '{msg['To']}' with subject '{subject}'{att_info}."
            logger.info(success_msg)
            return success_msg
        except Exception as e:
            logger.error(f"Error sending email via SMTP: {type(e).__name__}: {str(e)}", exc_info=True)
            try:
                smtp.quit()
            except Exception:
                pass
            return f"Error sending email: {type(e).__name__}: {str(e)}"

    def _execute_fetch_emails(self, params: dict) -> str:
        folder = params.get("folder", "INBOX")
        limit = min(int(params.get("limit", 10)), 50)
        unread_only = bool(params.get("unread_only", False))

        logger.debug(f"Fetching emails from folder '{folder}' (limit={limit}, unread_only={unread_only})")
        success, imap_or_err = self._get_imap_connection()
        if not success:
            logger.error(f"fetch_emails IMAP connection failed: {imap_or_err}")
            return f"Failed to connect to IMAP server: {imap_or_err}"

        imap = imap_or_err
        try:
            status, _ = imap.select(f'"{folder}"', readonly=True)
            if status != "OK":
                imap.logout()
                logger.error(f"fetch_emails failed: Folder '{folder}' could not be selected.")
                return f"Error: Mailbox folder '{folder}' could not be selected."

            search_criteria = "UNSEEN" if unread_only else "ALL"
            status, data = imap.uid("search", None, search_criteria)
            if status != "OK" or not data or not data[0]:
                imap.logout()
                logger.info(f"No {'unread ' if unread_only else ''}emails found in '{folder}'.")
                return f"No {'unread ' if unread_only else ''}emails found in '{folder}'."

            uids = data[0].split()
            selected_uids = uids[-limit:]
            selected_uids.reverse()

            results = []
            for uid_bytes in selected_uids:
                uid_str = uid_bytes.decode("ascii")
                res, msg_data = imap.uid("fetch", uid_bytes, "(BODY.PEEK[HEADER.FIELDS (SUBJECT FROM TO DATE MESSAGE-ID)] FLAGS)")
                if res != "OK" or not msg_data:
                    continue

                headers_raw = b""
                flags_str = ""
                for part in msg_data:
                    if isinstance(part, tuple):
                        headers_raw = part[1]
                        flags_str = str(part[0])

                parsed_msg = email.message_from_bytes(headers_raw, policy=policy.default)
                subject = _decode_mime_header(parsed_msg.get("Subject", "(No Subject)"))
                from_sender = _decode_mime_header(parsed_msg.get("From", "Unknown Sender"))
                date_str = parsed_msg.get("Date", "Unknown Date")
                is_read = "\\Seen" in flags_str

                results.append(
                    f"- [ID: {uid_str}] {'[READ]' if is_read else '[UNREAD]'} "
                    f"From: {from_sender} | Subject: '{subject}' | Date: {date_str}"
                )

            imap.logout()
            logger.info(f"Fetched {len(results)} email(s) from '{folder}'")
            header_text = f"Found {len(results)} {'unread ' if unread_only else ''}email(s) in '{folder}':"
            return header_text + "\n" + "\n".join(results)

        except Exception as e:
            logger.error(f"Error fetching emails: {type(e).__name__}: {str(e)}", exc_info=True)
            try:
                imap.logout()
            except Exception:
                pass
            return f"Error fetching emails: {type(e).__name__}: {str(e)}"

    def _execute_read_email(self, params: dict) -> str:
        email_id = str(params.get("email_id", "")).strip()
        folder = params.get("folder", "INBOX")
        mark_as_read = bool(params.get("mark_as_read", True))

        if not email_id:
            logger.error("read_email failed: 'email_id' is missing.")
            return "Error: 'email_id' is required to read an email."

        logger.debug(f"Reading email UID '{email_id}' from folder '{folder}' (mark_as_read={mark_as_read})")
        success, imap_or_err = self._get_imap_connection()
        if not success:
            logger.error(f"read_email IMAP connection failed: {imap_or_err}")
            return f"Failed to connect to IMAP server: {imap_or_err}"

        imap = imap_or_err
        try:
            status, _ = imap.select(f'"{folder}"', readonly=not mark_as_read)
            if status != "OK":
                imap.logout()
                logger.error(f"read_email failed: Folder '{folder}' could not be selected.")
                return f"Error: Mailbox folder '{folder}' could not be selected."

            status, data = imap.uid("fetch", email_id.encode("ascii"), "(RFC822)")
            if status != "OK" or not data or not data[0] or not isinstance(data[0], tuple):
                imap.logout()
                logger.error(f"read_email failed: UID '{email_id}' not found in folder '{folder}'.")
                return f"Error: Email with UID '{email_id}' not found in folder '{folder}'."

            raw_bytes = data[0][1]
            msg = email.message_from_bytes(raw_bytes, policy=policy.default)

            subject = _decode_mime_header(msg.get("Subject", "(No Subject)"))
            from_sender = _decode_mime_header(msg.get("From", "Unknown"))
            to_recipient = _decode_mime_header(msg.get("To", "Unknown"))
            date_str = msg.get("Date", "Unknown")
            message_id = msg.get("Message-ID", "")

            plain_body = ""
            html_body = ""
            attachments = []

            if msg.is_multipart():
                for part in msg.walk():
                    content_type = part.get_content_type()
                    content_disposition = str(part.get("Content-Disposition", ""))
                    filename = part.get_filename()

                    if filename or "attachment" in content_disposition:
                        fn = _decode_mime_header(filename or "unnamed_attachment")
                        payload_len = len(part.get_payload(decode=True) or b"")
                        attachments.append(f"{fn} ({content_type}, {payload_len} bytes)")
                    elif content_type == "text/plain" and not plain_body:
                        payload = part.get_payload(decode=True)
                        charset = part.get_content_charset() or "utf-8"
                        plain_body = payload.decode(charset, errors="replace") if payload else ""
                    elif content_type == "text/html" and not html_body:
                        payload = part.get_payload(decode=True)
                        charset = part.get_content_charset() or "utf-8"
                        html_body = payload.decode(charset, errors="replace") if payload else ""
            else:
                content_type = msg.get_content_type()
                payload = msg.get_payload(decode=True)
                charset = msg.get_content_charset() or "utf-8"
                if content_type == "text/plain":
                    plain_body = payload.decode(charset, errors="replace") if payload else ""
                elif content_type == "text/html":
                    html_body = payload.decode(charset, errors="replace") if payload else ""

            body_text = plain_body if plain_body.strip() else _html_to_plain_text(html_body)
            if not body_text.strip():
                body_text = "(Email body is empty or in unsupported format)"

            if mark_as_read:
                imap.uid("store", email_id.encode("ascii"), "+FLAGS", "\\Seen")

            imap.logout()
            logger.info(f"Successfully read email UID '{email_id}': '{subject}' from '{from_sender}' with {len(attachments)} attachment(s)")

            att_section = "\nAttachments:\n" + "\n".join(f"- {a}" for a in attachments) if attachments else "\nAttachments: None"

            return (
                f"=== Email Details (ID: {email_id}) ===\n"
                f"Subject: {subject}\n"
                f"From: {from_sender}\n"
                f"To: {to_recipient}\n"
                f"Date: {date_str}\n"
                f"Message-ID: {message_id}\n"
                f"{att_section}\n\n"
                f"--- Body ---\n"
                f"{body_text}"
            )

        except Exception as e:
            logger.error(f"Error reading email '{email_id}': {type(e).__name__}: {str(e)}", exc_info=True)
            try:
                imap.logout()
            except Exception:
                pass
            return f"Error reading email '{email_id}': {type(e).__name__}: {str(e)}"

    def _execute_search_emails(self, params: dict) -> str:
        folder = params.get("folder", "INBOX")
        query = params.get("query")
        sender = params.get("sender")
        recipient = params.get("recipient")
        subject = params.get("subject")
        since_date = params.get("since_date")
        limit = min(int(params.get("limit", 10)), 50)

        criteria_parts = []
        if query:
            criteria_parts.append(f'TEXT "{query}"')
        if sender:
            criteria_parts.append(f'FROM "{sender}"')
        if recipient:
            criteria_parts.append(f'TO "{recipient}"')
        if subject:
            criteria_parts.append(f'SUBJECT "{subject}"')
        if since_date:
            criteria_parts.append(f'SINCE "{since_date}"')

        search_criteria = " ".join(criteria_parts) if criteria_parts else "ALL"
        logger.debug(f"Searching emails in '{folder}' with criteria: {search_criteria}")

        success, imap_or_err = self._get_imap_connection()
        if not success:
            logger.error(f"search_emails IMAP connection failed: {imap_or_err}")
            return f"Failed to connect to IMAP server: {imap_or_err}"

        imap = imap_or_err
        try:
            status, _ = imap.select(f'"{folder}"', readonly=True)
            if status != "OK":
                imap.logout()
                logger.error(f"search_emails failed: Folder '{folder}' could not be selected.")
                return f"Error: Mailbox folder '{folder}' could not be selected."

            status, data = imap.uid("search", None, search_criteria)
            if status != "OK" or not data or not data[0]:
                imap.logout()
                logger.info(f"No emails matched criteria '{search_criteria}' in '{folder}'.")
                return f"No emails matched criteria '{search_criteria}' in '{folder}'."

            uids = data[0].split()
            selected_uids = uids[-limit:]
            selected_uids.reverse()

            results = []
            for uid_bytes in selected_uids:
                uid_str = uid_bytes.decode("ascii")
                res, msg_data = imap.uid("fetch", uid_bytes, "(BODY.PEEK[HEADER.FIELDS (SUBJECT FROM DATE)])")
                if res != "OK" or not msg_data:
                    continue

                headers_raw = b""
                for part in msg_data:
                    if isinstance(part, tuple):
                        headers_raw = part[1]

                parsed_msg = email.message_from_bytes(headers_raw, policy=policy.default)
                subj = _decode_mime_header(parsed_msg.get("Subject", "(No Subject)"))
                sndr = _decode_mime_header(parsed_msg.get("From", "Unknown"))
                date_str = parsed_msg.get("Date", "Unknown")

                results.append(f"- [ID: {uid_str}] From: {sndr} | Subject: '{subj}' | Date: {date_str}")

            imap.logout()
            logger.info(f"Search matched {len(results)} email(s) in '{folder}'")
            return f"Found {len(results)} match(es) for criteria '{search_criteria}':\n" + "\n".join(results)

        except Exception as e:
            logger.error(f"Error searching emails: {type(e).__name__}: {str(e)}", exc_info=True)
            try:
                imap.logout()
            except Exception:
                pass
            return f"Error searching emails: {type(e).__name__}: {str(e)}"

    def _execute_download_attachment(self, params: dict) -> str:
        email_id = str(params.get("email_id", "")).strip()
        attachment_filename = str(params.get("attachment_filename", "")).strip()
        save_directory = params.get("save_directory")
        folder = params.get("folder", "INBOX")

        if not email_id:
            logger.error("download_attachment failed: 'email_id' is missing.")
            return "Error: 'email_id' is required to download attachments."

        target_dir = os.path.expanduser(save_directory) if save_directory else self._get_agent_download_dir()
        os.makedirs(target_dir, exist_ok=True)
        logger.debug(f"Downloading attachment(s) '{attachment_filename or 'ALL'}' from email UID '{email_id}' in '{folder}' to '{target_dir}'")

        success, imap_or_err = self._get_imap_connection()
        if not success:
            logger.error(f"download_attachment IMAP connection failed: {imap_or_err}")
            return f"Failed to connect to IMAP server: {imap_or_err}"

        imap = imap_or_err
        try:
            status, _ = imap.select(f'"{folder}"', readonly=True)
            if status != "OK":
                imap.logout()
                logger.error(f"download_attachment failed: Folder '{folder}' could not be selected.")
                return f"Error: Mailbox folder '{folder}' could not be selected."

            status, data = imap.uid("fetch", email_id.encode("ascii"), "(RFC822)")
            if status != "OK" or not data or not data[0] or not isinstance(data[0], tuple):
                imap.logout()
                logger.error(f"download_attachment failed: Email UID '{email_id}' not found.")
                return f"Error: Email with UID '{email_id}' not found."

            raw_bytes = data[0][1]
            msg = email.message_from_bytes(raw_bytes, policy=policy.default)
            imap.logout()

            found_attachments = []
            part_idx = 1
            for part in msg.walk():
                if part.get_content_maintype() == 'multipart':
                    continue

                fn = part.get_filename()
                content_disposition = str(part.get("Content-Disposition", ""))
                content_type = part.get_content_type()

                if fn or "attachment" in content_disposition or (content_disposition and content_type != "text/plain" and content_type != "text/html"):
                    payload = part.get_payload(decode=True)
                    if payload is not None:
                        if fn:
                            decoded_fn = _decode_mime_header(fn)
                        else:
                            ext = mimetypes.guess_extension(content_type) or ".bin"
                            decoded_fn = f"attachment_{part_idx}{ext}"
                        found_attachments.append((decoded_fn, payload, content_type))
                        part_idx += 1

            if not found_attachments:
                logger.info(f"No attachments found in email '{email_id}'.")
                return f"No attachments found in email '{email_id}'."

            # Determine which attachments to save
            save_all = not attachment_filename or attachment_filename.lower() in ["all", "*"]
            to_save = []

            if save_all:
                to_save = found_attachments
            else:
                for fn, payload, ctype in found_attachments:
                    if fn.lower() == attachment_filename.lower() or attachment_filename.lower() in fn.lower():
                        to_save.append((fn, payload, ctype))

            if not to_save:
                avail = ", ".join(f"'{a[0]}'" for a in found_attachments)
                logger.error(f"download_attachment failed: Attachment '{attachment_filename}' not found in email '{email_id}'. Available: {avail}")
                return f"Error: Attachment '{attachment_filename}' not found in email '{email_id}'. Available attachments: {avail}"

            saved_results = []
            for fn, payload, ctype in to_save:
                clean_filename = re.sub(r'[/\\?%*:|"<>]', '_', fn)
                save_path = os.path.join(target_dir, clean_filename)
                with open(save_path, "wb") as f:
                    f.write(payload)
                size_kb = len(payload) / 1024
                logger.info(f"Saved attachment '{fn}' ({len(payload)} bytes) to '{save_path}'")
                saved_results.append(f"- {fn} ({size_kb:.1f} KB, {ctype}) -> {save_path}")

            if len(saved_results) == 1:
                return f"Attachment successfully downloaded and saved:\n{saved_results[0]}"
            else:
                return f"Successfully downloaded {len(saved_results)} attachment(s) to '{target_dir}':\n" + "\n".join(saved_results)

        except Exception as e:
            logger.error(f"Error downloading attachment: {type(e).__name__}: {str(e)}", exc_info=True)
            try:
                imap.logout()
            except Exception:
                pass
            return f"Error downloading attachment: {type(e).__name__}: {str(e)}"

    def _execute_mark_as_read(self, params: dict) -> str:
        email_id = str(params.get("email_id", "")).strip()
        read = bool(params.get("read", True))
        folder = params.get("folder", "INBOX")

        if not email_id:
            return "Error: 'email_id' is required."

        logger.debug(f"Marking email UID '{email_id}' as {'read' if read else 'unread'} in '{folder}'")
        success, imap_or_err = self._get_imap_connection()
        if not success:
            logger.error(f"mark_as_read IMAP connection failed: {imap_or_err}")
            return f"Failed to connect to IMAP server: {imap_or_err}"

        imap = imap_or_err
        try:
            imap.select(f'"{folder}"', readonly=False)
            flag_cmd = "+FLAGS" if read else "-FLAGS"
            status, _ = imap.uid("store", email_id.encode("ascii"), flag_cmd, "\\Seen")
            imap.logout()
            if status == "OK":
                logger.info(f"Updated read flag for email UID '{email_id}' to {read}")
                return f"Email '{email_id}' marked as {'read' if read else 'unread'}."
            logger.error(f"Failed to update flags for email UID '{email_id}'")
            return f"Failed to update flags for email '{email_id}'."
        except Exception as e:
            logger.error(f"Error updating email flags: {type(e).__name__}: {str(e)}", exc_info=True)
            try:
                imap.logout()
            except Exception:
                pass
            return f"Error: {type(e).__name__}: {str(e)}"

    def _execute_delete_email(self, params: dict) -> str:
        email_id = str(params.get("email_id", "")).strip()
        folder = params.get("folder", "INBOX")
        expunge = bool(params.get("expunge", True))

        if not email_id:
            return "Error: 'email_id' is required."

        logger.debug(f"Deleting email UID '{email_id}' in '{folder}' (expunge={expunge})")
        success, imap_or_err = self._get_imap_connection()
        if not success:
            logger.error(f"delete_email IMAP connection failed: {imap_or_err}")
            return f"Failed to connect to IMAP server: {imap_or_err}"

        imap = imap_or_err
        try:
            imap.select(f'"{folder}"', readonly=False)
            status, _ = imap.uid("store", email_id.encode("ascii"), "+FLAGS", "\\Deleted")
            if expunge and status == "OK":
                imap.expunge()
            imap.logout()
            logger.info(f"Deleted email UID '{email_id}' from '{folder}'")
            return f"Email '{email_id}' deleted successfully from folder '{folder}'."
        except Exception as e:
            logger.error(f"Error deleting email: {type(e).__name__}: {str(e)}", exc_info=True)
            try:
                imap.logout()
            except Exception:
                pass
            return f"Error: {type(e).__name__}: {str(e)}"

    def _execute_list_folders(self, params: dict) -> str:
        logger.debug("Listing mailbox folders...")
        success, imap_or_err = self._get_imap_connection()
        if not success:
            logger.error(f"list_folders IMAP connection failed: {imap_or_err}")
            return f"Failed to connect to IMAP server: {imap_or_err}"

        imap = imap_or_err
        try:
            status, folders = imap.list()
            imap.logout()
            if status != "OK" or not folders:
                logger.info("No mailbox folders found.")
                return "No mailbox folders found."

            folder_names = []
            for f in folders:
                if isinstance(f, bytes):
                    f_str = f.decode("utf-8", errors="replace")
                    folder_names.append(f"- {f_str}")
            logger.info(f"Discovered {len(folder_names)} mailbox folder(s)")
            return "Mailbox Folders:\n" + "\n".join(folder_names)
        except Exception as e:
            logger.error(f"Error listing folders: {type(e).__name__}: {str(e)}", exc_info=True)
            try:
                imap.logout()
            except Exception:
                pass
            return f"Error listing folders: {type(e).__name__}: {str(e)}"

    def get_tool_declarations(self) -> List[Dict[str, Any]]:
        return [
            {
                "name": "Email_send_email",
                "description": "Send an email from the agent's dedicated email address.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "to": {
                            "type": "STRING",
                            "description": "Recipient email address (or comma-separated list of addresses)."
                        },
                        "subject": {
                            "type": "STRING",
                            "description": "Subject line of the email."
                        },
                        "body": {
                            "type": "STRING",
                            "description": "Plain text content of the email body."
                        },
                        "html_body": {
                            "type": "STRING",
                            "description": "Optional rich text HTML version of the body."
                        },
                        "cc": {
                            "type": "STRING",
                            "description": "Optional CC recipient email address(es)."
                        },
                        "bcc": {
                            "type": "STRING",
                            "description": "Optional BCC recipient email address(es)."
                        },
                        "reply_to": {
                            "type": "STRING",
                            "description": "Optional Reply-To email address."
                        },
                        "in_reply_to_message_id": {
                            "type": "STRING",
                            "description": "Optional Message-ID header of the email being replied to for proper thread tracking."
                        },
                        "attachments": {
                            "type": "ARRAY",
                            "items": {"type": "STRING"},
                            "description": "Optional list of file paths (or filenames in agent folders) to attach to the outgoing email (max 25MB total)."
                        }
                    },
                    "required": ["to", "subject", "body"]
                }
            },
            {
                "name": "Email_fetch_emails",
                "description": "Fetch a list of recent emails from a mailbox folder (default INBOX).",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "folder": {
                            "type": "STRING",
                            "description": "Mailbox folder to fetch from (default 'INBOX')."
                        },
                        "limit": {
                            "type": "INTEGER",
                            "description": "Maximum number of recent emails to retrieve (default 10, max 50)."
                        },
                        "unread_only": {
                            "type": "BOOLEAN",
                            "description": "If true, only returns unread emails. Defaults to false."
                        }
                    }
                }
            },
            {
                "name": "Email_read_email",
                "description": "Read the full contents, headers, and attachment list of a specific email by its ID.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "email_id": {
                            "type": "STRING",
                            "description": "The UID of the email to read (from fetch_emails or search_emails)."
                        },
                        "folder": {
                            "type": "STRING",
                            "description": "Mailbox folder (default 'INBOX')."
                        },
                        "mark_as_read": {
                            "type": "BOOLEAN",
                            "description": "Whether to mark the email as read/seen on the server. Defaults to true."
                        }
                    },
                    "required": ["email_id"]
                }
            },
            {
                "name": "Email_search_emails",
                "description": "Search the mailbox for emails matching specific criteria.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "query": {
                            "type": "STRING",
                            "description": "Text keyword to search in email subject and body."
                        },
                        "sender": {
                            "type": "STRING",
                            "description": "Filter by sender email address or name."
                        },
                        "recipient": {
                            "type": "STRING",
                            "description": "Filter by recipient email address."
                        },
                        "subject": {
                            "type": "STRING",
                            "description": "Filter by keywords in subject line."
                        },
                        "since_date": {
                            "type": "STRING",
                            "description": "Filter emails received since date (format: DD-Mon-YYYY e.g. '01-Jan-2026')."
                        },
                        "folder": {
                            "type": "STRING",
                            "description": "Mailbox folder to search in (default 'INBOX')."
                        },
                        "limit": {
                            "type": "INTEGER",
                            "description": "Maximum results to return (default 10)."
                        }
                    }
                }
            },
            {
                "name": "Email_download_attachment",
                "description": "Download an attachment from an email and save it to the agent's files.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "email_id": {
                            "type": "STRING",
                            "description": "The UID of the email containing the attachment."
                        },
                        "attachment_filename": {
                            "type": "STRING",
                            "description": "Optional filename of the specific attachment to download. If omitted or set to 'all', downloads all attachments from the email."
                        },
                        "save_directory": {
                            "type": "STRING",
                            "description": "Optional directory to save the file(s). Defaults to ~/Downloads/<AgentName>/."
                        },
                        "folder": {
                            "type": "STRING",
                            "description": "Mailbox folder (default 'INBOX')."
                        }
                    },
                    "required": ["email_id"]
                }
            },
            {
                "name": "Email_mark_as_read",
                "description": "Mark an email as read or unread on the server.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "email_id": {
                            "type": "STRING",
                            "description": "The UID of the email."
                        },
                        "read": {
                            "type": "BOOLEAN",
                            "description": "True to mark as read, False to mark as unread (default True)."
                        },
                        "folder": {
                            "type": "STRING",
                            "description": "Mailbox folder (default 'INBOX')."
                        }
                    },
                    "required": ["email_id"]
                }
            },
            {
                "name": "Email_delete_email",
                "description": "Mark an email for deletion and expunge it from the mailbox.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "email_id": {
                            "type": "STRING",
                            "description": "The UID of the email to delete."
                        },
                        "folder": {
                            "type": "STRING",
                            "description": "Mailbox folder (default 'INBOX')."
                        },
                        "expunge": {
                            "type": "BOOLEAN",
                            "description": "Whether to permanently expunge immediately. Defaults to true."
                        }
                    },
                    "required": ["email_id"]
                }
            },
            {
                "name": "Email_list_folders",
                "description": "List all available mailbox folders on the IMAP server.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {}
                }
            }
        ]
