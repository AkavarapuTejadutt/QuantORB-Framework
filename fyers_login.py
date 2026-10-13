"""
Auto-login and session management for FYERS API v3.

Usage:
    from fyers_login import login
    fyers, session = login()

`fyers` is an authenticated FyersModel object ready for data and order calls.
`session` holds the access token and login timestamp.

Run directly to test the login:
    python fyers_login.py
"""

import json
import logging
import os
import sys
import urllib.parse
import webbrowser
from datetime import datetime
from pathlib import Path

from fyers_apiv3 import fyersModel

log = logging.getLogger("fyers_login")

SESSION_FILE = Path(__file__).with_name(".fyers_session.json")
ENV_FILE = Path(__file__).with_name(".env")


def _mask(value):
    """Show only first 2 and last 2 characters of an identifier."""
    value = str(value or "")
    if len(value) <= 4:
        return "*" * len(value)
    return value[:2] + "*" * (len(value) - 4) + value[-2:]


def load_env_file():
    """Load key-value pairs from .env into os.environ."""
    if ENV_FILE.exists():
        try:
            for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    key = k.strip()
                    val = v.strip().strip("'").strip('"')
                    if key and val:
                        os.environ[key] = val
        except Exception:
            pass


def get_creds():
    """Return FYERS credentials dictionary from environment or defaults."""
    load_env_file()
    app_id = (os.environ.get("FYERS_CLIENT_ID", "").strip() or 
              os.environ.get("FYERS_APP_ID", "").strip() or 
              "cKNPUI0IK79-100")

    secret_id = (os.environ.get("FYERS_SECRET_KEY", "").strip() or 
                 os.environ.get("FYERS_SECRET_ID", "").strip() or 
                 "NB74EL61EH")

    redirect_uri = os.environ.get("FYERS_REDIRECT_URI", "").strip() or "http://127.0.0.1"
    return {
        "app_id": app_id,
        "secret_id": secret_id,
        "redirect_uri": redirect_uri,
    }


def extract_auth_code_from_input(input_str: str) -> str:
    """Extract clean auth_code whether user pasted raw code or full redirect URL."""
    clean = input_str.strip().strip("'").strip('"')
    if "auth_code=" in clean or "http" in clean:
        try:
            parsed = urllib.parse.urlparse(clean)
            params = urllib.parse.parse_qs(parsed.query)
            if "auth_code" in params:
                return params["auth_code"][0]
            if "?" in clean:
                query = clean.split("?", 1)[1]
                params = urllib.parse.parse_qs(query)
                if "auth_code" in params:
                    return params["auth_code"][0]
        except Exception:
            pass
    return clean


def _verify_session(client_id: str, access_token: str):
    """Test if an access token is valid by making a profile request."""
    try:
        fyers = fyersModel.FyersModel(
            client_id=client_id,
            is_async=False,
            token=access_token,
            log_path="",
        )
        profile = fyers.get_profile()
        if profile and isinstance(profile, dict) and profile.get("s") == "ok":
            return fyers, profile
    except Exception as exc:
        log.warning("FYERS session verification failed: %s", exc)
    return None, None


def _obtain_fresh_token(creds: dict):
    """Generate authorization URL, open browser, and exchange auth code for access token."""
    load_env_file()
    
    # Check if access token is already provided in env
    env_token = os.environ.get("FYERS_ACCESS_TOKEN", "").strip()
    if env_token:
        fyers, prof = _verify_session(creds["app_id"], env_token)
        if fyers:
            log.info("Using valid FYERS_ACCESS_TOKEN from environment")
            return env_token, fyers, prof

    session_model = fyersModel.SessionModel(
        client_id=creds["app_id"],
        secret_key=creds["secret_id"],
        redirect_uri=creds["redirect_uri"],
        response_type="code",
        grant_type="authorization_code",
    )

    auth_url = session_model.generate_authcode()
    print("\n" + "=" * 70)
    print("FYERS API AUTHORIZATION REQUIRED")
    print("=" * 70)
    print(f"App ID      : {creds['app_id']}")
    print(f"Redirect URI: {creds['redirect_uri']}")
    print("\nOpening your web browser for FYERS login...")
    print(f"\n   {auth_url}\n")
    print("INSTRUCTIONS:")
    print("1. Log in using the FYERS account that created this App in your dashboard.")
    print("2. Once logged in, FYERS redirects to http://127.0.0.1/?auth_code=eyJ...")
    print("   (Note: Page showing 'Can't reach site' is 100% NORMAL!).")
    print("3. COPY the full redirected URL from your browser address bar and PASTE it below.\n")

    try:
        webbrowser.open(auth_url)
    except Exception as exc:
        log.warning("Could not auto-open browser: %s", exc)

    raw_input = input("Paste full redirected URL or auth_code here: ").strip()
    if not raw_input:
        raise RuntimeError("No input provided. Authorization cancelled.")

    auth_code = extract_auth_code_from_input(raw_input)
    if len(auth_code) < 15:
        raise RuntimeError(
            f"You entered '{raw_input}'. That is too short to be a valid FYERS authorization code or URL.\n"
            "Please copy the FULL URL from your browser address bar after logging in (it starts with http://127.0.0.1/?auth_code=...) and paste it here."
        )

    session_model.set_token(auth_code)
    response = session_model.generate_token()

    if not response or not isinstance(response, dict) or response.get("s") != "ok":
        msg = (response or {}).get("message", response)
        raise RuntimeError(f"Failed to generate FYERS token: {msg}")

    access_token = response["access_token"]
    fyers, prof = _verify_session(creds["app_id"], access_token)
    if not fyers:
        # Retry verification with stripped/added 'c' prefix
        alt_id = creds["app_id"].removeprefix("c") if creds["app_id"].startswith("c") else "c" + creds["app_id"]
        fyers_alt, prof_alt = _verify_session(alt_id, access_token)
        if fyers_alt:
            return access_token, fyers_alt, prof_alt
        raise RuntimeError("Generated access token failed profile verification")

    return access_token, fyers, prof


def update_access_token_in_env(token: str) -> None:
    """Save newly generated access token into .env file."""
    token_str = token.strip()
    lines = []
    found = False
    if ENV_FILE.exists():
        with open(ENV_FILE, "r", encoding="utf-8") as f:
            for line in f:
                if line.startswith("FYERS_ACCESS_TOKEN="):
                    lines.append(f"FYERS_ACCESS_TOKEN={token_str}\n")
                    found = True
                else:
                    lines.append(line)
    if not found:
        lines.append(f"FYERS_ACCESS_TOKEN={token_str}\n")

    with open(ENV_FILE, "w", encoding="utf-8") as f:
        f.writelines(lines)

    os.environ["FYERS_ACCESS_TOKEN"] = token_str


def login(force=False):
    """
    Return (fyers_client, session_data).
    Reuses today's saved session if still valid, otherwise guides token generation.
    """
    creds = get_creds()

    if not force and SESSION_FILE.exists():
        try:
            saved = json.loads(SESSION_FILE.read_text())
            same_day = saved.get("login_time", "")[:10] == datetime.now().strftime("%Y-%m-%d")
            same_app = saved.get("app_id") == creds["app_id"]
            if same_day and same_app and saved.get("access_token"):
                fyers, prof = _verify_session(creds["app_id"], saved["access_token"])
                if fyers:
                    log.info("Reused saved FYERS session from %s", saved["login_time"])
                    return fyers, saved
        except Exception:
            pass

    access_token, fyers, profile = _obtain_fresh_token(creds)
    session_data = {
        "app_id": creds["app_id"],
        "access_token": access_token,
        "login_time": datetime.now().isoformat(timespec="seconds"),
        "profile": profile.get("data", {}),
    }
    SESSION_FILE.write_text(json.dumps(session_data, indent=2))
    update_access_token_in_env(access_token)
    return fyers, session_data


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        fyers, session = login()
        prof = session.get("profile", {})
        print("\n" + "=" * 50)
        print("FYERS LOGIN SUCCESSFUL!")
        print("=" * 50)
        print(f"  App ID    : {session.get('app_id')}")
        print(f"  User Name : {prof.get('name', 'N/A')}")
        print(f"  FYERS ID  : {_mask(prof.get('fy_id', ''))}")
        print(f"  Email     : {prof.get('email_id', 'N/A')}")
        print(f"  Login at  : {session.get('login_time')}")
        print("=" * 50)
    except Exception as e:
        print(f"\n[ERROR] Authentication failed: {e}")
