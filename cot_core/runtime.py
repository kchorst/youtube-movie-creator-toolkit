import sys


def is_interactive() -> bool:
    """Return True if stdin appears to be an interactive terminal."""
    try:
        stdin = sys.stdin
        return bool(stdin and hasattr(stdin, "isatty") and stdin.isatty())
    except Exception:
        return False


def safe_input(prompt: str = "", default: str = "") -> str:
    """Like input(), but never blocks/crashes in headless/non-interactive runs."""
    if not is_interactive():
        return default
    try:
        return input(prompt)
    except EOFError:
        return default
