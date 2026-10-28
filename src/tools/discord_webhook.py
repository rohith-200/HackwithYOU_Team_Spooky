"""
discord_webhook.py: post incident alerts to the venue Discord channel via a webhook.
(Replaces One, per organizer instructions.)

    ok = await post_alert("team-spooky · catering_coffee_bar · evacuate · ...")

Keep DISCORD_WEBHOOK_URL in .env only: anyone with the URL can post to the channel.
"""
import asyncio
import os

import httpx
from dotenv import load_dotenv

load_dotenv()

WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
MAX_LEN = 2000  # Discord message limit


async def post_alert(content: str, retries: int = 2) -> bool:
    """Send one message. Returns True if Discord accepted it. Never raises."""
    if not WEBHOOK_URL:
        print("  Discord: DISCORD_WEBHOOK_URL not set, alert not sent")
        return False
    payload = {"content": content[:MAX_LEN], "allowed_mentions": {"parse": []}}  # no @everyone pings
    async with httpx.AsyncClient(timeout=10) as client:
        for attempt in range(retries + 1):
            try:
                r = await client.post(WEBHOOK_URL, json=payload)
                if r.status_code in (200, 204):
                    return True
                if r.status_code == 429:  # rate limited: Discord says how long to wait
                    wait = float((r.json() or {}).get("retry_after", 1.0))
                    await asyncio.sleep(min(wait, 5.0))
                    continue
                print(f"  Discord: HTTP {r.status_code} {r.text[:150]}")
                return False
            except Exception as e:
                if attempt == retries:
                    print(f"  Discord: {type(e).__name__}: {e}")
                    return False
                await asyncio.sleep(1.0)
    return False


if __name__ == "__main__":  # quick test: python -m src.tools.discord_webhook
    team = os.environ.get("TEAM_HANDLE", "team-xx")
    ok = asyncio.run(post_alert(f"{team} · test · Ghost Ops swarm online 👻"))
    print("sent" if ok else "failed")