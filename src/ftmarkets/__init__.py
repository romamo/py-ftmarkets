from importlib.metadata import version

from pydantic_market_data.models import OHLCV, History, Security

from .api import FTDataSource as FTDataSource

__all__ = ["OHLCV", "History", "Security", "FTDataSource"]

__version__ = version("py-ftmarkets")
