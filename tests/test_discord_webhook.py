import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from src.tools.discord_webhook import post_alert


class DiscordWebhookTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_or_untrusted_webhook_does_not_make_a_request(self):
        with patch.dict(os.environ, {"DISCORD_WEBHOOK_URL": ""}), \
                patch("src.tools.discord_webhook.httpx.AsyncClient") as client:
            self.assertFalse(await post_alert("test"))
            client.assert_not_called()

        with patch.dict(os.environ, {
            "DISCORD_WEBHOOK_URL": "https://attacker.example/api/webhooks/private"
        }), patch("src.tools.discord_webhook.httpx.AsyncClient") as client:
            self.assertFalse(await post_alert("test"))
            client.assert_not_called()

    async def test_success_posts_without_allowing_mentions_and_truncates(self):
        response = SimpleNamespace(status_code=204)
        client = AsyncMock()
        client.post.return_value = response
        manager = MagicMock()
        manager.__aenter__ = AsyncMock(return_value=client)
        manager.__aexit__ = AsyncMock(return_value=False)

        with patch.dict(os.environ, {
            "DISCORD_WEBHOOK_URL": "https://discord.com/api/webhooks/123/secret"
        }), patch("src.tools.discord_webhook.httpx.AsyncClient", return_value=manager):
            self.assertTrue(await post_alert("@everyone " + "x" * 2100))

        client.post.assert_awaited_once()
        request = client.post.await_args
        self.assertEqual(request.args[0], "https://discord.com/api/webhooks/123/secret")
        self.assertEqual(request.kwargs["json"]["allowed_mentions"], {"parse": []})
        self.assertEqual(len(request.kwargs["json"]["content"]), 2000)

    async def test_http_failure_is_reported_as_not_sent(self):
        client = AsyncMock()
        client.post.return_value = SimpleNamespace(status_code=403)
        manager = MagicMock()
        manager.__aenter__ = AsyncMock(return_value=client)
        manager.__aexit__ = AsyncMock(return_value=False)

        with patch.dict(os.environ, {
            "DISCORD_WEBHOOK_URL": "https://discord.com/api/webhooks/123/secret"
        }), patch("src.tools.discord_webhook.httpx.AsyncClient", return_value=manager):
            self.assertFalse(await post_alert("test", retries=0))


if __name__ == "__main__":
    unittest.main()
