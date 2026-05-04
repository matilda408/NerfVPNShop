import html
import json
import re

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import HTMLResponse

from src.core.utils.happ import HAPP_LINK_PREFIX, HAPP_REDIRECT_PATH

router = APIRouter()
_HAPP_LINK_RE = re.compile(rf"^{re.escape(HAPP_LINK_PREFIX)}[A-Za-z0-9+/=]+$")


@router.get(HAPP_REDIRECT_PATH)
async def happ_redirect(url: str = Query(..., min_length=len(HAPP_LINK_PREFIX) + 1)) -> HTMLResponse:
    if not _HAPP_LINK_RE.fullmatch(url):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST)

    escaped_url = html.escape(url, quote=True)

    return HTMLResponse(
        content=f"""
<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Happ</title>
  <meta http-equiv="refresh" content="0; url={escaped_url}">
  <script>window.location.replace({json.dumps(url)});</script>
</head>
<body>
  <a href="{escaped_url}">Открыть Happ</a>
</body>
</html>
""",
        status_code=status.HTTP_200_OK,
    )
