"""The ``ftmarkets`` entry point where the treaty CLI cannot run"""

import importlib.util
import sys

import pytest

from ftmarkets.cli import main

CLI_AVAILABLE = sys.version_info >= (3, 14) and importlib.util.find_spec("treaty") is not None


@pytest.mark.skipif(CLI_AVAILABLE, reason="the treaty CLI is installed here")
def test_main_without_the_cli_exits_1_naming_the_fix(capsys):
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 1
    err = capsys.readouterr().err
    running = ".".join(str(part) for part in sys.version_info[:3])
    assert f"Python {running}" in err
    assert 'pip install "py-ftmarkets[cli]"' in err


def test_import_does_not_need_treaty():
    import ftmarkets

    assert ftmarkets.FTDataSource is not None
