# run.py
# Start the trading engine first. It reuses a valid saved Shoonya token.
# The browser OAuth flow is called only when no valid saved session exists.

from get_auth import get_auth_code
from Extreme_Reversal_Signal import main


if __name__ == "__main__":
    main(auth_code_provider=get_auth_code)
