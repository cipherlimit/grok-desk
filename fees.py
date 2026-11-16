"""FOMO fees: 0.50% per trade, 10% off via referral -> 0.45%. Gas paid by FOMO."""
BASE_FEE = 0.0050
REF_DISCOUNT = 0.10
FEE = BASE_FEE * (1 - REF_DISCOUNT)   # 0.0045

def fee(usd: float) -> float:
    return round(usd * FEE, 6)

def paper_pnl(size_usd: float, entry: float, exit_: float) -> tuple[float, float]:
    """Returns (pnl_usd after fees on both sides, fees_usd)."""
    buy_fee = fee(size_usd)
    tokens = (size_usd - buy_fee) / entry
    gross_out = tokens * exit_
    sell_fee = fee(gross_out)
    return round(gross_out - sell_fee - size_usd, 6), round(buy_fee + sell_fee, 6)
