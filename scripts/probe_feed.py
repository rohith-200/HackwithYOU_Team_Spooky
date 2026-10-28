"""
probe_feed.py: ask the scenario MCP server what tools it has and what arguments they take.
Safe to run: it only lists tools and reads the scoreboard (no tick fetch, no submission).

Run:  python scripts/probe_feed.py
"""
import asyncio
import json
import os

from dotenv import load_dotenv
from mcp import ClientSession
try:
    from mcp.client.streamable_http import streamable_http_client as http_client
except ImportError:
    from mcp.client.streamable_http import streamablehttp_client as http_client

load_dotenv()
URL = os.environ["SCENARIO_MCP"]


async def main():
    print(f"Connecting to {URL} ...")
    async with http_client(URL) as streams:
        read, write = streams[0], streams[1]
        async with ClientSession(read, write) as session:
            await session.initialize()

            tools = (await session.list_tools()).tools
            print(f"\n{len(tools)} tools found:\n")
            for t in tools:
                print(f"--- {t.name} ---")
                print(t.description or "(no description)")
                schema = getattr(t, "input_schema", None) or getattr(t, "inputSchema", None)
                print("input schema:", json.dumps(schema, indent=2, default=str))
                print()

            names = [t.name for t in tools]
            board = next((n for n in names if "scoreboard" in n), None)
            if board:
                print(f"--- calling {board} (read-only) ---")
                res = await session.call_tool(board, {})
                for block in res.content:
                    print(getattr(block, "text", block))


if __name__ == "__main__":
    asyncio.run(main())