"""Entry point of the ``ftmarkets`` console script

The CLI is built on treaty, which needs Python 3.14 and the ``cli`` extra. This module
imports nothing from treaty at import time, so ``import ftmarkets`` keeps working on
every Python the library supports; the treaty app lives in ``ftmarkets.cli_app``.
"""

import sys

_MIN_PYTHON = (3, 14)
_INSTALL_HINT = 'pip install "py-ftmarkets[cli]"'


def _running_python() -> str:
    return ".".join(str(part) for part in sys.version_info[:3])


def main() -> None:
    if sys.version_info < _MIN_PYTHON:
        sys.stderr.write(
            f"ftmarkets: the CLI needs Python 3.14 or newer; this is Python {_running_python()}. "
            f"Install it on Python 3.14 with: {_INSTALL_HINT}\n"
        )
        raise SystemExit(1)
    try:
        from .cli_app import app
    except ModuleNotFoundError as exc:
        if exc.name != "treaty":
            raise
        sys.stderr.write(
            f"ftmarkets: the CLI needs the 'cli' extra (treaty), which is not installed "
            f"for Python {_running_python()}. Install it with: {_INSTALL_HINT}\n"
        )
        raise SystemExit(1) from None
    app.main()


if __name__ == "__main__":
    main()
