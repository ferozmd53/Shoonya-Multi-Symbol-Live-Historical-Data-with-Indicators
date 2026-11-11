# run.py
# Run this file from the project folder.
# get_auth.py captures the Shoonya OAuth code and stores it in LOGIN!B7.
# Extreme_Reversal_Signal.py then exchanges that code for the API session.

from get_auth import get_auth_code
from Extreme_Reversal_Signal import main


if __name__ == "__main__":
    auth_code = get_auth_code()
    if not auth_code:
        raise SystemExit("❌ OAuth authorization code was not received. Login cancelled.")

    main()
