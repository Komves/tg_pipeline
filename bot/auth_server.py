from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse, JSONResponse
import os
import json
from pathlib import Path
from google_auth_oauthlib.flow import Flow

app = FastAPI()

DATA_DIR = Path(os.getenv("DATA_DIR", "/tmp"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

CLIENT_ID = os.getenv("GMAIL_CLIENT_ID")
CLIENT_SECRET = os.getenv("GMAIL_CLIENT_SECRET")
REDIRECT_URI = os.getenv("GMAIL_REDIRECT_URI")

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]


def _client_config():
    return {
        "web": {
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
        }
    }


@app.get("/start")
def start(user_id: int):
    flow = Flow.from_client_config(
        _client_config(),
        scopes=SCOPES,
        redirect_uri=REDIRECT_URI,
    )

    auth_url, state = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
    )

    # сохраняем state → user_id
    (DATA_DIR / f"state_{state}.json").write_text(
        json.dumps({"user_id": user_id}),
        encoding="utf-8",
    )

    return RedirectResponse(auth_url)


@app.get("/oauth")
async def oauth(request: Request):
    try:
        state = request.query_params.get("state")

        flow = Flow.from_client_config(
            _client_config(),
            scopes=SCOPES,
            redirect_uri=REDIRECT_URI,
        )

        flow.fetch_token(authorization_response=str(request.url))
        creds = flow.credentials

        state_file = DATA_DIR / f"state_{state}.json"
        if not state_file.exists():
            return JSONResponse({"error": "state not found"}, status_code=400)

        data = json.loads(state_file.read_text())
        user_id = data["user_id"]

        token_file = DATA_DIR / f"gmail_{user_id}.json"
        token_file.write_text(creds.to_json(), encoding="utf-8")

        return JSONResponse({"status": "ok", "message": "Gmail подключен"})

    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)