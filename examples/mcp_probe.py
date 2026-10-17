"""Exercise the real MCP protocol without npm or a real agent."""

import argparse
import asyncio
import json
import sys
from datetime import timedelta

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client


async def probe(read, write, project):
    async with ClientSession(read, write, read_timeout_seconds=timedelta(seconds=20)) as session:
        await session.initialize()
        tools = await session.list_tools()
        print(json.dumps({"tools": [t.name for t in tools.tools]}, ensure_ascii=False, indent=2))
        result = await session.call_tool("bootstrap_project", {"project_id": project})
        print(json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2))
        if result.isError:
            raise SystemExit(1)


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--home")
    parser.add_argument("--url")
    parser.add_argument("--project", required=True)
    args = parser.parse_args()
    if args.url:
        async with streamable_http_client(args.url) as (read, write, _):
            await probe(read, write, args.project)
    else:
        options = ["-m", "agentbridge_mcp"]
        if args.home:
            options += ["--home", args.home]
        async with stdio_client(
            StdioServerParameters(command=sys.executable, args=options + ["serve"])
        ) as (read, write):
            await probe(read, write, args.project)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    asyncio.run(main())
