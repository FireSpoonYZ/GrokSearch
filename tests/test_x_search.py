import os
import unittest
from unittest.mock import AsyncMock, patch

from fastmcp import Client
from fastmcp.exceptions import ToolError

from grok_search import server
from grok_search.providers import searxng
from grok_search.providers.grok import GrokSearchProvider


class XSearchTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {
            "SEARXNG_BASE_URL": "http://searx.test",
            "GROK_API_URL": "https://grok.test/v1",
            "GROK_API_KEY": "test-key", "GUDA_API_KEY": "",
        })
        self.env.start()
        self.addCleanup(self.env.stop)

    async def test_provider_enables_only_native_x_search(self):
        provider = GrokSearchProvider("https://grok.test/v1", "test-key", "grok-test")
        with patch.object(provider, "_execute_stream_with_retry", new_callable=AsyncMock, return_value="posts") as execute:
            await provider.search("from:example", "X/Twitter", tool_type="x_search")
        payload = execute.call_args.args[1]
        self.assertEqual(payload["tools"], [{"type": "x_search"}])
        self.assertEqual(execute.call_args.kwargs["endpoint"], "/responses")
        self.assertIn("provided x_search", payload["instructions"])

    async def test_mcp_x_search_bypasses_searxng_and_shares_source_cache(self):
        with patch.object(GrokSearchProvider, "search", new_callable=AsyncMock,
                          return_value="A post.\n\nSources\n- https://x.com/example/status/123") as search, patch.object(
            server, "_get_available_models_cached", new_callable=AsyncMock, return_value=["grok-test"]
        ), patch.object(searxng, "search", new_callable=AsyncMock) as web:
            async with Client(server.mcp) as client:
                tools = await client.list_tools()
                self.assertTrue({"web_search", "x_search", "get_sources"} <= {t.name for t in tools})
                data = (await client.call_tool("x_search", {"query": "from:example", "model": "grok-test"})).data
                sources = (await client.call_tool("get_sources", {"session_id": data["session_id"]})).data
        search.assert_awaited_once_with("from:example", "X/Twitter", tool_type="x_search")
        web.assert_not_called()
        self.assertEqual(data["search_tool"], "x_search")
        self.assertEqual(data["content"], "A post.")
        self.assertEqual(data["sources_count"], 1)
        self.assertEqual(sources["sources"][0]["url"], "https://x.com/example/status/123")

    async def test_explicit_invalid_model_does_not_search(self):
        with patch.object(server, "_get_available_models_cached", new_callable=AsyncMock,
                          return_value=["grok-test"]), patch.object(
            GrokSearchProvider, "search", new_callable=AsyncMock
        ) as search:
            result = await server.x_search("from:example", model="unknown")
        search.assert_not_called()
        self.assertEqual(result["sources_count"], 0)
        self.assertIn("无效模型", result["content"])

    async def test_x_search_failure_does_not_fall_back_to_web(self):
        with patch.object(GrokSearchProvider, "search", new_callable=AsyncMock,
                          side_effect=RuntimeError("x_search unavailable")), patch.object(
            searxng, "search", new_callable=AsyncMock
        ) as web:
            result = await server.x_search("from:example")
        web.assert_not_called()
        self.assertEqual(result["error"], "grok_search_failed")
        self.assertEqual(result["search_tool"], "x_search")
        self.assertIn("检查上游是否支持原生 x_search", result["content"])

    async def test_mcp_rejects_empty_query(self):
        async with Client(server.mcp) as client:
            with self.assertRaises(ToolError):
                await client.call_tool("x_search", {"query": ""})


if __name__ == "__main__":
    unittest.main()
