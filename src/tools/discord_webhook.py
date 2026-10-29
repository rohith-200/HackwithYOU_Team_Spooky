"""Send incident alerts to the configured Discord webhook."""

import asyncio
import os
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

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


def _is_message_id(value) -> bool:
    return (isinstance(value, str) and 1 <= len(value) <= 20 and value.isascii()
            and value.isdigit() and int(value) > 0)


async def post_alert(content: str, retries: int = 2) -> bool:
    """Post one message and require Discord's confirmed message receipt."""
    url = _webhook_url()
    if url is None:
        return False

    parsed = urlsplit(url)
    query = [(key, value) for key, value in parse_qsl(parsed.query, keep_blank_values=True)
             if key.casefold() != "wait"]
    query.append(("wait", "true"))
    url = urlunsplit(parsed._replace(query=urlencode(query)))

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

            if response.status_code == 200:
                try:
                    receipt = response.json()
                except (ValueError, TypeError):
                    receipt = None
                if (not isinstance(receipt, dict) or not _is_message_id(receipt.get("id"))
                        or not _is_message_id(receipt.get("channel_id"))):
                    print("  Discord: no valid message receipt; delivery could not be confirmed")
                    return False
                print(f"  Discord: confirmed message {receipt['id']} in channel {receipt['channel_id']}")
                if _is_message_id(receipt.get("guild_id")):
                    print(f"  Discord message: https://discord.com/channels/{receipt['guild_id']}/"
                          f"{receipt['channel_id']}/{receipt['id']}")
                return True
            if response.status_code == 204:
                print("  Discord: HTTP 204 without a message receipt; delivery could not be confirmed")
                return False
            if response.status_code == 429 and attempt + 1 < attempts:
                await asyncio.sleep(_retry_after(response))
                continue
            if 500 <= response.status_code < 600 and attempt + 1 < attempts:
                await asyncio.sleep(1.0)
                continue

            print(f"  Discord: webhook returned HTTP {response.status_code}")
            return False

    return False
