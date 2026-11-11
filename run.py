"""Start Shoonya OAuth authentication, then launch the live Excel terminal."""

from get_auth import get_auth_code


def main():
    auth_code = get_auth_code()
    if not auth_code:
        print("Shoonya authentication did not return an OAuth code. Trading system not started.")
        return

    # Import after authentication so the trading module does not open Excel
    # until the authentication step has completed.
    from Extreme_Reversal_Signal import main as run_trading_terminal
    run_trading_terminal()


if __name__ == "__main__":
    main()
