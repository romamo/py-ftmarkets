from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field, RootModel
from pydantic_market_data.models import Ticker as Ticker


class Xid(RootModel):
    root: str

    def __str__(self) -> str:
        return self.root


# Ticker is imported from pydantic_market_data.models


class Isin(RootModel):
    root: str

    def __str__(self) -> str:
        return self.root


class DataPeriod(str, Enum):
    DAY = "Day"
    WEEK = "Week"
    MONTH = "Month"


class ReturnDateType(str, Enum):
    ISO8601 = "ISO8601"


class TimeServiceFormat(str, Enum):
    JSON = "JSON"


class ChartElementType(str, Enum):
    PRICE = "price"
    VOLUME = "volume"


class ChartElementParams(BaseModel):
    """
    Parameters for chart elements — intentionally empty.
    Included to match the FT Chart API request contract ("Params": {}).
    """

    pass


class ChartRequestElement(BaseModel):
    """
    An individual element requested in the chart API.
    Example: Price or Volume for a specific symbol (xid).
    """

    type: ChartElementType = Field(..., alias="Type")
    symbol: Xid = Field(..., alias="Symbol")  # API expects string ID
    overlay_indicators: list[str] = Field(default_factory=list, alias="OverlayIndicators")
    params: ChartElementParams = Field(default_factory=ChartElementParams, alias="Params")


class ChartRequest(BaseModel):
    """
    Payload for the FT Markets Chart API.
    Endpoint: /data/chartapi/series
    """

    days: int = Field(..., description="Number of days of history to fetch")
    data_period: DataPeriod = Field(default=DataPeriod.DAY, alias="dataPeriod")
    data_interval: int = Field(default=1, alias="dataInterval")
    realtime: bool = Field(default=False)
    y_format: str = Field(default="0.###", alias="yFormat")
    time_service_format: TimeServiceFormat = Field(
        default=TimeServiceFormat.JSON, alias="timeServiceFormat"
    )
    return_date_type: ReturnDateType = Field(default=ReturnDateType.ISO8601, alias="returnDateType")
    elements: list[ChartRequestElement]


class ComponentSeriesType(str, Enum):
    OPEN = "Open"
    HIGH = "High"
    LOW = "Low"
    CLOSE = "Close"
    VOLUME = "Volume"


class ComponentSeries(BaseModel):
    """
    A single series of values (e.g., all Open prices).
    """

    type: ComponentSeriesType = Field(..., alias="Type")
    values: list[float] = Field(..., alias="Values")


class ChartElementResponse(BaseModel):
    """
    Response data for a single requested element.
    Contains the component series (OHLC or Volume).
    """

    type: ChartElementType = Field(..., alias="Type")
    symbol: str = Field(..., alias="Symbol")
    component_series: list[ComponentSeries] = Field(default_factory=list, alias="ComponentSeries")


class ChartResponse(BaseModel):
    """
    Full response from the Chart API.
    """

    dates: list[datetime] = Field(..., alias="Dates")
    elements: list[ChartElementResponse] = Field(..., alias="Elements")
