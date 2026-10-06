"""Forex research and MetaTrader 5 execution.

`study` tests strategy rules on historical forex/metal/crypto CFD data with
per-market costs and pre-registered pass gates; only rules that pass are
written to `approved_strategies.json`, and `mt5_trader` trades nothing else.
"""
