"""FastAPI app for plan A (local HTTPS). Not served under plan B; kept so the tree is complete."""
from fastapi import FastAPI, Header, HTTPException
import json
from config import OUT_DIR, secret

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

def _auth(token):
    if token != secret("APP_TOKEN"): raise HTTPException(404)

@app.get("/api/{name}.json")
def data(name: str, x_app_token: str | None = Header(None)):
    _auth(x_app_token)
    if name not in {"state", "chart", "activity"}: raise HTTPException(404)
    return json.loads((OUT_DIR / f"{name}.json").read_text())
