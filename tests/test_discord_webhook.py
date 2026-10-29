from contextlib import redirect_stdout
import io
import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import parse_qsl, urlsplit

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
        receipt = {"id": "1564842098740695101", "channel_id": "1564842098740695100",
                   "guild_id": "958041025539080202"}
        response = SimpleNamespace(status_code=200, json=lambda: receipt)
        client = AsyncMock()
        client.post.return_value = response
        manager = MagicMock()
        manager.__aenter__ = AsyncMock(return_value=client)
        manager.__aexit__ = AsyncMock(return_value=False)

        diagnostics = io.StringIO()
        with patch.dict(os.environ, {
            "DISCORD_WEBHOOK_URL": "https://discord.com/api/webhooks/123/secret"
        }), patch("src.tools.discord_webhook.httpx.AsyncClient", return_value=manager), \
                redirect_stdout(diagnostics):
            self.assertTrue(await post_alert("@everyone " + "x" * 2100))

        client.post.assert_awaited_once()
        request = client.post.await_args
        self.assertEqual(request.args[0], "https://discord.com/api/webhooks/123/secret?wait=true")
        self.assertEqual(request.kwargs["json"]["allowed_mentions"], {"parse": []})
        self.assertEqual(len(request.kwargs["json"]["content"]), 2000)
        self.assertIn("https://discord.com/channels/958041025539080202/"
                      "1564842098740695100/1564842098740695101", diagnostics.getvalue())
        self.assertNotIn("secret", diagnostics.getvalue())
        self.assertNotIn("@everyone", diagnostics.getvalue())

    async def test_wait_is_overridden_while_thread_and_other_query_params_are_preserved(self):
        response = SimpleNamespace(status_code=200, json=lambda: {
            "id": "1564842098740695101", "channel_id": "1564842098740695100",
        })
        client = AsyncMock()
        client.post.return_value = response
        manager = MagicMock()
        manager.__aenter__ = AsyncMock(return_value=client)
        manager.__aexit__ = AsyncMock(return_value=False)
        with patch.dict(os.environ, {
            "DISCORD_WEBHOOK_URL": "https://discord.com/api/webhooks/123/secret"
                                   "?thread_id=456&wait=false&with_components=true&wait=false&empty="
        }), patch("src.tools.discord_webhook.httpx.AsyncClient", return_value=manager):
            self.assertTrue(await post_alert("test"))

        query = parse_qsl(urlsplit(client.post.await_args.args[0]).query, keep_blank_values=True)
        self.assertEqual(query, [("thread_id", "456"), ("with_components", "true"),
                                 ("empty", ""), ("wait", "true")])

    async def test_unconfirmed_204_is_not_success_and_is_not_retried(self):
        client = AsyncMock()
        client.post.return_value = SimpleNamespace(status_code=204)
        manager = MagicMock()
        manager.__aenter__ = AsyncMock(return_value=client)
        manager.__aexit__ = AsyncMock(return_value=False)
        with patch.dict(os.environ, {
            "DISCORD_WEBHOOK_URL": "https://discord.com/api/webhooks/123/secret"
        }), patch("src.tools.discord_webhook.httpx.AsyncClient", return_value=manager), \
                patch("src.tools.discord_webhook.asyncio.sleep", new_callable=AsyncMock) as sleep:
            self.assertFalse(await post_alert("test"))
        client.post.assert_awaited_once()
        sleep.assert_not_awaited()

    async def test_malformed_or_missing_receipts_are_not_success_and_are_not_retried(self):
        receipts = [None, [], {}, {"id": "123"}, {"channel_id": "456"},
                    {"id": "0", "channel_id": "456"},
                    {"id": "123", "channel_id": "not-an-id"},
                    {"id": 123, "channel_id": "456"}]
        for receipt in receipts:
            with self.subTest(receipt=receipt):
                client = AsyncMock()
                client.post.return_value = SimpleNamespace(status_code=200, json=lambda: receipt)
                manager = MagicMock()
                manager.__aenter__ = AsyncMock(return_value=client)
                manager.__aexit__ = AsyncMock(return_value=False)
                with patch.dict(os.environ, {
                    "DISCORD_WEBHOOK_URL": "https://discord.com/api/webhooks/123/secret"
                }), patch("src.tools.discord_webhook.httpx.AsyncClient", return_value=manager), \
                        patch("src.tools.discord_webhook.asyncio.sleep", new_callable=AsyncMock) as sleep:
                    self.assertFalse(await post_alert("test"))
                client.post.assert_awaited_once()
                sleep.assert_not_awaited()

    async def test_invalid_receipt_json_is_not_retried(self):
        client = AsyncMock()
        client.post.return_value = SimpleNamespace(
            status_code=200, json=MagicMock(side_effect=ValueError("invalid JSON")))
        manager = MagicMock()
        manager.__aenter__ = AsyncMock(return_value=client)
        manager.__aexit__ = AsyncMock(return_value=False)
        with patch.dict(os.environ, {
            "DISCORD_WEBHOOK_URL": "https://discord.com/api/webhooks/123/secret"
        }), patch("src.tools.discord_webhook.httpx.AsyncClient", return_value=manager), \
                patch("src.tools.discord_webhook.asyncio.sleep", new_callable=AsyncMock) as sleep:
            self.assertFalse(await post_alert("test"))
        client.post.assert_awaited_once()
        sleep.assert_not_awaited()

    async def test_rate_limit_retries_then_requires_a_confirmed_receipt(self):
        client = AsyncMock()
        client.post.side_effect = [
            SimpleNamespace(status_code=429, json=lambda: {"retry_after": 0.25}),
            SimpleNamespace(status_code=200, json=lambda: {
                "id": "1564842098740695101", "channel_id": "1564842098740695100",
            }),
        ]
        manager = MagicMock()
        manager.__aenter__ = AsyncMock(return_value=client)
        manager.__aexit__ = AsyncMock(return_value=False)
        with patch.dict(os.environ, {
            "DISCORD_WEBHOOK_URL": "https://discord.com/api/webhooks/123/secret"
        }), patch("src.tools.discord_webhook.httpx.AsyncClient", return_value=manager), \
                patch("src.tools.discord_webhook.asyncio.sleep", new_callable=AsyncMock) as sleep:
            self.assertTrue(await post_alert("test"))
        self.assertEqual(client.post.await_count, 2)
        sleep.assert_awaited_once_with(0.25)

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
