import os
import base64
import time
import logging
import urllib.parse
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading
import webbrowser
from typing import Optional, Dict, Tuple, Any
import requests

logger = logging.getLogger("core.EmailAuth")

DEFAULT_GOOGLE_AUTH_URI = "https://accounts.google.com/o/oauth2/v2/auth"
DEFAULT_GOOGLE_TOKEN_URI = "https://oauth2.googleapis.com/token"
DEFAULT_EMAIL_SCOPES = ["https://mail.google.com/"]


def build_xoauth2_string(user_email: str, access_token: str, as_base64: bool = False) -> Any:
    """
    Constructs the standard SASL XOAUTH2 authentication string (RFC 7628 / Google SASL).
    Format: user={user_email}\x01auth=Bearer {access_token}\x01\x01
    """
    auth_str = f"user={user_email}\x01auth=Bearer {access_token}\x01\x01"
    if as_base64:
        return base64.b64encode(auth_str.encode("utf-8")).decode("ascii")
    return auth_str.encode("utf-8")


class OAuth2TokenManager:
    """Manages retrieval and refreshing of OAuth2 access tokens."""

    def __init__(self):
        self._access_token: Optional[str] = None
        self._expires_at: float = 0.0
        self._lock = threading.Lock()

    def get_access_token(
        self,
        client_id: str,
        client_secret: str,
        refresh_token: str,
        token_uri: str = DEFAULT_GOOGLE_TOKEN_URI
    ) -> Tuple[bool, str]:
        """
        Retrieves a valid access token. Refreshes the token automatically if expired or missing.
        Returns: (success: bool, token_or_error_message: str)
        """
        if not client_id or not client_secret or not refresh_token:
            return False, "Missing OAuth2 client_id, client_secret, or refresh_token."

        with self._lock:
            # Check if current token is still valid (with 60s buffer)
            if self._access_token and time.time() < (self._expires_at - 60):
                return True, self._access_token

            try:
                payload = {
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "refresh_token": refresh_token,
                    "grant_type": "refresh_token"
                }
                headers = {"Content-Type": "application/x-www-form-urlencoded"}
                resp = requests.post(token_uri, data=payload, headers=headers, timeout=15)
                
                if resp.status_code != 200:
                    err_msg = f"OAuth2 token refresh failed ({resp.status_code}): {resp.text}"
                    logger.error(err_msg)
                    return False, err_msg

                data = resp.json()
                self._access_token = data.get("access_token")
                expires_in = data.get("expires_in", 3600)
                self._expires_at = time.time() + float(expires_in)

                if not self._access_token:
                    return False, "OAuth2 server response did not contain an access_token."

                return True, self._access_token

            except Exception as e:
                err_msg = f"Exception refreshing OAuth2 token: {e}"
                logger.error(err_msg, exc_info=True)
                return False, err_msg


class OAuthCallbackHandler(BaseHTTPRequestHandler):
    """Local HTTP request handler to capture OAuth2 authorization code callback."""
    code: Optional[str] = None
    error: Optional[str] = None

    def log_message(self, format, *args):
        # Suppress standard HTTP server access logs
        return

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)

        if "code" in params:
            OAuthCallbackHandler.code = params["code"][0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            html = """
            <!DOCTYPE html>
            <html>
            <head><title>Open Amity - Authorization Successful</title></head>
            <body style="font-family: sans-serif; text-align: center; padding: 50px; background: #1e1e1e; color: #fff;">
                <h2 style="color: #4CAF50;">Authentication Successful!</h2>
                <p>Open Amity has received the authorization code. You can now close this browser tab.</p>
            </body>
            </html>
            """
            self.wfile.write(html.encode("utf-8"))
        elif "error" in params:
            OAuthCallbackHandler.error = params["error"][0]
            self.send_response(400)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            err = params['error'][0]
            html = f"""
            <!DOCTYPE html>
            <html>
            <head><title>Open Amity - Authorization Failed</title></head>
            <body style="font-family: sans-serif; text-align: center; padding: 50px; background: #1e1e1e; color: #fff;">
                <h2 style="color: #F44336;">Authentication Failed</h2>
                <p>Error: {err}</p>
            </body>
            </html>
            """
            self.wfile.write(html.encode("utf-8"))
        else:
            self.send_response(404)
            self.end_headers()


def exchange_authorization_code(
    client_id: str,
    client_secret: str,
    code: str,
    redirect_uri: str = "http://localhost:8080/callback",
    token_uri: str = DEFAULT_GOOGLE_TOKEN_URI
) -> Tuple[bool, Dict[str, Any]]:
    """
    Exchanges an OAuth2 authorization code for access and refresh tokens.
    """
    if not client_id or not client_secret or not code:
        return False, {"error": "Client ID, Client Secret, and Authorization Code are required."}

    # If full URL was passed by mistake, extract the code param
    if "code=" in code:
        parsed = urllib.parse.urlparse(code)
        params = urllib.parse.parse_qs(parsed.query)
        if "code" in params:
            code = params["code"][0]

    try:
        token_payload = {
            "code": code.strip(),
            "client_id": client_id.strip(),
            "client_secret": client_secret.strip(),
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code"
        }
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        resp = requests.post(token_uri, data=token_payload, headers=headers, timeout=15)
        
        if resp.status_code != 200:
            err_msg = f"Failed to exchange authorization code ({resp.status_code}): {resp.text}"
            logger.error(err_msg)
            return False, {"error": err_msg}

        tokens = resp.json()
        return True, tokens

    except Exception as e:
        logger.error(f"Error exchanging OAuth authorization code: {e}", exc_info=True)
        return False, {"error": f"Error exchanging authorization code: {e}"}


def execute_desktop_oauth_flow(
    client_id: str,
    client_secret: str,
    auth_uri: str = DEFAULT_GOOGLE_AUTH_URI,
    token_uri: str = DEFAULT_GOOGLE_TOKEN_URI,
    scopes: Optional[list] = None,
    port: int = 8080,
    timeout_seconds: int = 300
) -> Tuple[bool, Dict[str, Any]]:
    """
    Executes a local loopback OAuth2 authorization code flow for desktop client setup.
    Opens browser for user authentication, captures redirect, and exchanges code for tokens.
    Returns: (success: bool, token_data_or_error: dict)
    """
    if not client_id or not client_secret:
        return False, {"error": "Client ID and Client Secret are required for OAuth2 flow."}

    scopes = scopes or DEFAULT_EMAIL_SCOPES
    redirect_uri = f"http://localhost:{port}/callback"

    OAuthCallbackHandler.code = None
    OAuthCallbackHandler.error = None

    server = None
    for bind_host in ["127.0.0.1", "localhost", "0.0.0.0"]:
        try:
            server = HTTPServer((bind_host, port), OAuthCallbackHandler)
            server.timeout = 2.0
            break
        except Exception:
            continue

    if not server:
        return False, {"error": f"Failed to bind local OAuth callback server on port {port}."}

    params = {
        "client_id": client_id.strip(),
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(scopes),
        "access_type": "offline",
        "prompt": "consent"
    }
    auth_url = f"{auth_uri}?{urllib.parse.urlencode(params)}"

    logger.info(f"Launching browser for OAuth2 authorization: {auth_url}")
    webbrowser.open(auth_url)

    start_time = time.time()
    while time.time() - start_time < timeout_seconds:
        server.handle_request()
        if OAuthCallbackHandler.code or OAuthCallbackHandler.error:
            break

    try:
        server.server_close()
    except Exception:
        pass

    if OAuthCallbackHandler.error:
        return False, {"error": f"OAuth authorization denied: {OAuthCallbackHandler.error}"}

    if not OAuthCallbackHandler.code:
        return False, {"error": "OAuth authorization timed out (5 minutes) without receiving a callback code."}

    return exchange_authorization_code(
        client_id=client_id,
        client_secret=client_secret,
        code=OAuthCallbackHandler.code,
        redirect_uri=redirect_uri,
        token_uri=token_uri
    )
