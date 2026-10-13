"""
Build a versioned, credential-free zip of this project for sharing.

    python make_zip.py

Reads the version from VERSION.txt and writes
    releases/nifty_orb_v<version>_<date>.zip
Only the files listed in INCLUDE go in. Session tokens, logs, state,
trade history and the instrument cache are never included.
"""

import datetime as dt
import re
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).parent
INCLUDE = [
    "README.txt", "CHANGELOG.txt", "VERSION.txt", "requirements.txt", "run.bat",
    "setup_credentials.py", "config.py", "main.py", "replay.py", "angel_login.py",
    "strategy.py", "market_data.py", "instruments.py", "broker.py", ".gitignore",
    "make_zip.py",
]
# Anything matching these must never appear in the package.
FORBIDDEN_PATTERNS = [
    r"eyJ[A-Za-z0-9._-]{20,}",            # JWT tokens
]
# The packager's own real credentials, read from the environment (and the
# Windows registry, in case this terminal predates setx). Any file containing
# one of these values is refused.
CRED_VARS = ("ANGEL_API_KEY", "ANGEL_TOTP_SECRET", "ANGEL_CLIENT_CODE", "ANGEL_MPIN")


def _real_credentials():
    import os
    values = {os.environ.get(v, "").strip() for v in CRED_VARS}
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment")
        for v in CRED_VARS:
            try:
                values.add(str(winreg.QueryValueEx(key, v)[0]).strip())
            except OSError:
                pass
    except ImportError:
        pass
    return {v for v in values if len(v) >= 4}


def main():
    version = (ROOT / "VERSION.txt").read_text().strip()
    name = f"nifty_orb_v{version}_{dt.date.today():%Y-%m-%d}.zip"
    out_dir = ROOT / "releases"
    out_dir.mkdir(exist_ok=True)
    out = out_dir / name

    missing = [f for f in INCLUDE if not (ROOT / f).exists()]
    if missing:
        sys.exit(f"missing files: {missing}")

    creds = _real_credentials()
    for f in INCLUDE:
        text = (ROOT / f).read_text(encoding="utf-8", errors="ignore")
        for pat in FORBIDDEN_PATTERNS:
            if re.search(pat, text):
                sys.exit(f"refusing to package {f}: matches secret pattern {pat}")
        if any(c in text for c in creds):
            sys.exit(f"refusing to package {f}: it contains one of your real credential values")
        if f == "config.py" and re.search(r"PAPER_TRADING\s*=\s*False", text):
            print("note: config.py has PAPER_TRADING = False; packaging it as True for safety")

    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for f in INCLUDE:
            data = (ROOT / f).read_bytes()
            if f == "config.py":
                data = re.sub(rb"PAPER_TRADING\s*=\s*False", b"PAPER_TRADING = True", data)
            z.writestr(f"nifty_orb/{f}", data)

    print(f"created {out.relative_to(ROOT)}  ({out.stat().st_size // 1024} KB, {len(INCLUDE)} files)")


if __name__ == "__main__":
    main()
