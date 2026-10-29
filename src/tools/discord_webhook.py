"""Send incident alerts to the configured Discord webhook."""

import asyncio
import os
from urllib.parse import urlsplit

import httpx
from dotenv import load_dotenv

load_dotenv()

MAX_LEN = 2000
ALLOWED_HOSTS = {"discord.com", "discordapp.com"}


def _webhook_url() -> str | None:
    value = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
    if not value:
        print("  Discord: DISCORD_WEBHOOK_URL is not set")
        return None

    try:
        parsed = urlsplit(value)
        valid = (
            parsed.scheme == "https"
            and parsed.hostname in ALLOWED_HOSTS
            and parsed.path.startswith("/api/webhooks/")
            and parsed.username is None
            and parsed.password is None
            and parsed.port in (None, 443)
        )
    except ValueError:
        valid = False

    if not valid:
        print("  Discord: DISCORD_WEBHOOK_URL must be an HTTPS Discord webhook URL")
        return None
    return value


def _retry_after(response: httpx.Response) -> float:
    try:
        payload = response.json()
        delay = payload.get("retry_after") if isinstance(payload, dict) else None
        if delay is not None:
            return max(0.0, min(float(delay), 5.0))
    except (ValueError, TypeError, UnicodeError):
        pass

    try:
        return max(0.0, min(float(response.headers.get("Retry-After", "1")), 5.0))
    except ValueError:
        return 1.0


async def post_alert(content: str, retries: int = 2) -> bool:
    """Post one message, returning False with an explicit diagnostic on failure."""
    url = _webhook_url()
    if url is None:
        return False

    payload = {
        "content": content[:MAX_LEN],
        "allowed_mentions": {"parse": []},
    }
    attempts = max(0, retries) + 1
    async with httpx.AsyncClient(timeout=10) as client:
        for attempt in range(attempts):
            try:
                response = await client.post(url, json=payload)
            except httpx.HTTPError as error:
                if attempt + 1 < attempts:
                    await asyncio.sleep(1.0)
                    continue
                print(f"  Discord: request failed ({type(error).__name__})")
                return False

            if response.status_code in (200, 204):
                return True
            if response.status_code == 429 and attempt + 1 < attempts:
                await asyncio.sleep(_retry_after(response))
                continue
            if 500 <= response.status_code < 600 and attempt + 1 < attempts:
                await asyncio.sleep(1.0)
                continue

            print(f"  Discord: webhook returned HTTP {response.status_code}")
            return False

    return False
