"""FOMO fee model on Solana for PAPER PnL (STEP 11). Gas = 0, FOMO pays it.
Per side, on that side's ticket:  $5 to $47.50 -> 2% of the ticket
                                  $47.50 to $190 -> flat $0.95
                                  above $190     -> 0.50%
then minus fees_ref_discount (10% off through the r=savipww referral)."""
REF_DISCOUNT = 0.10
TIER1_MAX, TIER2_MAX, FLAT = 47.50, 190.0, 0.95

def side_fee(ticket_usd: float, ref_discount: float = REF_DISCOUNT) -> float:
    t = abs(ticket_usd)
    if t <= 0: return 0.0
    if t < TIER1_MAX: base = 0.02 * t          # under $5 is not in the table: same 2% (stricter than 0)
    elif t <= TIER2_MAX: base = FLAT
    else: base = 0.005 * t
    return round(base * (1 - ref_discount), 6)

def fee(usd: float, ref_discount: float = REF_DISCOUNT) -> float:
    return side_fee(usd, ref_discount)

def paper_pnl(size_usd: float, entry: float, exit_: float, ref_discount: float = REF_DISCOUNT) -> tuple[float, float]:
    """Returns (pnl_usd after fees on both sides, fees_usd). Buy fee comes out of the ticket."""
    buy_fee = side_fee(size_usd, ref_discount)
    tokens = (size_usd - buy_fee) / entry
    gross_out = tokens * exit_
    sell_fee = side_fee(gross_out, ref_discount)
    return round(gross_out - sell_fee - size_usd, 6), round(buy_fee + sell_fee, 6)
