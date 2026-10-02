"""The README's Python examples run against the current API

The default run hands each ``FTDataSource()`` in the example a fake scraper by rewriting
the call in the example's syntax tree; the ``live`` run executes the example unchanged.
"""

import ast
import re
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from pydantic_market_data.models import OHLCV, History, Security, Symbol

README = Path(__file__).resolve().parents[2] / "README.md"
FAKE_SCRAPER = "_readme_fake_scraper"


def python_blocks() -> list[str]:
    blocks = re.findall(r"```python\n(.*?)```", README.read_text(encoding="utf-8"), re.S)
    if not blocks:
        raise ValueError(f"{README} has no python code blocks")
    return blocks


class FakeScraper:
    """Answers every search with Xetra-Gold, and every history request with daily candles
    around 83.5 up to today"""

    def search(self, query: str) -> list[Security]:
        return [Security(symbol="4GLD:GER:EUR", name="Xetra-Gold", isin="DE000A0S9GB0")]

    def get_history(self, symbol: Symbol, days: int = 30) -> History:
        today = datetime.combine(datetime.now().date(), datetime.min.time())
        candles = [
            OHLCV(date=today - timedelta(days=n), open=83.5, high=84, low=83, close=83.5)
            for n in range(days, -1, -1)
        ]
        return History(security=Security(symbol=symbol, name=symbol.root), candles=candles)


class InjectFakeScraper(ast.NodeTransformer):
    """``FTDataSource()`` becomes ``FTDataSource(scraper_instance=<fake>)``"""

    def __init__(self) -> None:
        self.injected = 0

    def visit_Call(self, node: ast.Call) -> ast.AST:
        self.generic_visit(node)
        if isinstance(node.func, ast.Name) and node.func.id == "FTDataSource":
            if node.args or node.keywords:
                raise ValueError("the README builds FTDataSource with arguments; update the test")
            node.keywords.append(
                ast.keyword(arg="scraper_instance", value=ast.Name(FAKE_SCRAPER, ast.Load()))
            )
            self.injected += 1
        return node


@pytest.mark.parametrize("block", python_blocks())
def test_readme_example_runs_against_a_fake_scraper(block, capsys):
    transformer = InjectFakeScraper()
    tree = ast.fix_missing_locations(transformer.visit(ast.parse(block)))
    assert transformer.injected > 0, "the example builds no FTDataSource()"

    exec(compile(tree, str(README), "exec"), {FAKE_SCRAPER: FakeScraper()})

    out = capsys.readouterr().out
    assert "Symbol: 4GLD:GER:EUR" in out
    assert "Price valid: True" in out


@pytest.mark.live
@pytest.mark.parametrize("block", python_blocks())
def test_readme_example_runs_live(block):
    exec(compile(block, str(README), "exec"), {})
