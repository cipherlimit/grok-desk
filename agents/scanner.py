"""Agent: scanner. Stub for STEP 3; full logic is built in its own step."""
from agents.base import idle_loop, agent_logger
NAME = "scanner"
log = agent_logger(NAME)

async def run(conn, bus, settings):
    log.info("start (stub)")
    await idle_loop(NAME, conn)
