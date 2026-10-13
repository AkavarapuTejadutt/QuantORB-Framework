"""
One-time helper: stores your FYERS API and Angel One credentials as environment
variables so the algo can log in. Nothing is written to any file.

    python setup_credentials.py

After it finishes, CLOSE this terminal and open a new one.
"""

import getpass
import platform
import subprocess
import sys

FIELDS_FYERS = [
    ("FYERS_APP_ID", "FYERS App ID (from api-dashboard.fyers.in, e.g. KNPUOI0K79-100)", "KNPUOI0K79-100"),
    ("FYERS_SECRET_ID", "FYERS Secret ID (e.g. NB74EL61EH)", "NB74EL61EH"),
    ("FYERS_REDIRECT_URI", "FYERS Redirect URL (e.g. http://127.0.0.1)", "http://127.0.0.1"),
]

FIELDS_ANGEL = [
    ("ANGEL_API_KEY", "Angel One SmartAPI API key"),
    ("ANGEL_CLIENT_CODE", "Angel One client code (e.g. A123456)"),
    ("ANGEL_MPIN", "4-digit MPIN used in the Angel One app"),
    ("ANGEL_TOTP_SECRET", "TOTP secret text (~26 chars)"),
]


def main():
    print("=========================================================")
    print("  FYERS API & BROKER CREDENTIAL SETUP HELPER")
    print("=========================================================\n")
    print("Choice:\n  1) Setup FYERS API credentials (Recommended)\n  2) Setup Angel One credentials\n  3) Setup Both\n")
    choice = input("Enter choice (1, 2, or 3) [default 1]: ").strip() or "1"

    fields_to_prompt = []
    if choice in ("1", "3"):
        fields_to_prompt.extend(FIELDS_FYERS)
    if choice in ("2", "3"):
        fields_to_prompt.extend(FIELDS_ANGEL)

    values = {}
    for item in fields_to_prompt:
        name = item[0]
        prompt = item[1]
        default_val = item[2] if len(item) > 2 else ""

        if default_val:
            v = input(f"{prompt} [Press Enter for default: {default_val}]: ").strip() or default_val
        else:
            while True:
                v = getpass.getpass(f"{prompt}\n  {name}: ").strip()
                if v:
                    break
                print("  value cannot be empty")
        values[name] = v

    if platform.system() == "Windows":
        for name, v in values.items():
            r = subprocess.run(["setx", name, v], capture_output=True, text=True)
            status = "saved" if r.returncode == 0 else f"FAILED: {r.stderr.strip()}"
            print(f"  {name}: {status}")
        print("\nDone. Close this window, open a NEW PowerShell window, then run:\n"
              "    python main.py")
    else:
        rc = "~/.zshrc" if "zsh" in (subprocess.os.environ.get("SHELL") or "") else "~/.bashrc"
        print(f"\nAdd these lines to {rc}, then open a new terminal:\n")
        for name, v in values.items():
            print(f'export {name}="{v}"')


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit("\ncancelled")
