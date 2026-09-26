"""Market Edge Laboratory: pre-registered, point-in-time market research.

This package is deliberately isolated from live execution: it never imports the
broker, Flask app, scheduler, Telegram, Turso or AI decision modules, and it
places no orders. A backtest verdict is at most ELIGIBLE_FOR_SHADOW; nothing
here can mark a strategy ready for live money.
"""
