"""Night report (stub): totals from db.positions for the Telegram summary."""
import db

def night_summary(conn) -> dict:
    r = conn.execute("SELECT COUNT(*) n, COALESCE(SUM(pnl_usd),0) pnl FROM positions WHERE status='closed'").fetchone()
    return {"closed": r["n"], "pnl_usd": round(r["pnl"], 2)}
