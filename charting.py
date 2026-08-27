import base64
import io
import logging

import mplfinance as mpf
import pandas as pd

logger = logging.getLogger(__name__)


def bars_to_chart_base64(bars: list[dict], title: str = "") -> str | None:
    """Renders a candlestick+volume chart from our bar format (most-recent-first
    list of dicts with datetime/open/high/low/close/volume) and returns a base64
    PNG data URI, or None if there isn't enough data to plot."""
    if len(bars) < 5:
        return None

    try:
        df = pd.DataFrame(list(reversed(bars)))  # mplfinance needs chronological order
        df["datetime"] = pd.to_datetime(df["datetime"], unit="ms")
        df = df.set_index("datetime")
        for col in ("open", "high", "low", "close", "volume"):
            df[col] = df[col].astype(float)

        buf = io.BytesIO()
        mpf.plot(df, type="candle", volume=True, style="charles", title=title,
                  savefig=dict(fname=buf, format="png", dpi=100))
        buf.seek(0)
        return base64.b64encode(buf.read()).decode()
    except Exception:
        logger.exception("Failed to render chart for %s", title)
        return None
