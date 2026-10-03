import json
import os
import unittest
from unittest.mock import AsyncMock, patch

import httpx
from fastmcp import Client
from fastmcp.exceptions import ToolError

from grok_search import server
from grok_search.config import config
from grok_search.providers import searxng
from grok_search.providers.grok import GrokSearchProvider

RESULTS = [
    {"url": "https://github.com/example/engine", "title": "Engine", "content": "Specialized runtime."},
    {"url": "https://example.org/bench", "title": "Bench", "content": "Measured performance."},
]


class SearxNGTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {
            "SEARXNG_BASE_URL": "http://searx.test/",
            "SEARXNG_ENGINES": "bing,github",
            "SEARXNG_SUMMARIZE": "false",
            "GROK_API_URL": "", "GROK_API_KEY": "", "GUDA_API_KEY": "",
        })
        self.env.start()
        self.addCleanup(self.env.stop)

    def client(self, handler):
        # Real httpx request/response handling, only transport is replaced.
        original = httpx.AsyncClient
        return patch("httpx.AsyncClient", side_effect=lambda **kwargs: original(
            **kwargs, transport=httpx.MockTransport(handler)
        ))

    async def test_request_platform_limit_and_normalization(self):
        def handler(request):
            self.assertEqual(request.url.path, "/search")
            self.assertEqual(request.url.params["format"], "json")
            self.assertEqual(request.url.params["engines"], "bing,github")
            self.assertIn("(site:github.com OR site:reddit.com)", request.url.params["q"])
            return httpx.Response(200, json={"results": [
                {"url": "javascript:bad"}, {"url": "http://[broken"}, {"title": "no URL"},
                {"url": "https://github.com/example/engine", "title": None, "content": None},
                RESULTS[0], RESULTS[1],
            ], "unresponsive_engines": [["brave", "CAPTCHA"]]})

        with self.client(handler):
            sources, unavailable = await searxng.search("runtime", "GitHub, Reddit", 2)
        self.assertEqual(len(sources), 2)
        self.assertEqual(sources[0]["title"], "")
        self.assertEqual(sources[0]["description"], "")
        self.assertEqual(sources[0]["provider"], "searxng")
        self.assertEqual(unavailable, [["brave", "CAPTCHA"]])

    async def test_mcp_search_without_keys_and_cached_sources(self):
        with self.client(lambda request: httpx.Response(200, json={"results": RESULTS})):
            async with Client(server.mcp) as client:
                response = await client.call_tool("web_search", {"query": "runtime"})
                data = response.data
                cached = (await client.call_tool("get_sources", {"session_id": data["session_id"]})).data
        self.assertEqual(data["sources_count"], 2)
        self.assertIn(RESULTS[0]["url"], data["content"])
        self.assertEqual(cached["sources"][0]["url"], RESULTS[0]["url"])
        self.assertEqual(cached["sources_count"], 2)

    async def test_no_results_reports_upstream_engine_errors_without_grok(self):
        with self.client(lambda request: httpx.Response(200, json={
            "results": [], "unresponsive_engines": [["duckduckgo", "CAPTCHA"]]
        })), patch.object(GrokSearchProvider, "summarize", new_callable=AsyncMock) as summary:
            data = await server.web_search("runtime")
        self.assertEqual(data["sources_count"], 0)
        self.assertIn("CAPTCHA", data["content"])
        summary.assert_not_called()

    async def test_http_failure_is_not_a_successful_empty_search(self):
        with self.client(lambda request: httpx.Response(403)):
            data = await server.web_search("runtime")
        self.assertEqual(data["error"], "searxng_search_failed")
        self.assertIn("403", data["content"])

    async def test_bad_json_shape_is_reported(self):
        with self.client(lambda request: httpx.Response(200, json={"results": "not a list"})):
            data = await server.web_search("runtime")
        self.assertEqual(data["error"], "searxng_search_failed")

    async def test_summary_failure_preserves_real_sources(self):
        with patch.dict(os.environ, {"SEARXNG_SUMMARIZE": "true",
                                    "GROK_API_URL": "https://grok.test/v1", "GROK_API_KEY": "test-key"}):
            with self.client(lambda request: httpx.Response(200, json={"results": RESULTS})), patch.object(
                GrokSearchProvider, "summarize", new_callable=AsyncMock, side_effect=RuntimeError("offline")
            ):
                data = await server.web_search("runtime")
        self.assertEqual(data["sources_count"], 2)
        self.assertIn("summary_error", data)
        self.assertIn(RESULTS[1]["url"], data["content"])

    async def test_summary_cannot_add_fabricated_sources_to_cache(self):
        with patch.dict(os.environ, {"SEARXNG_SUMMARIZE": "true",
                                    "GROK_API_URL": "https://grok.test/v1", "GROK_API_KEY": "test-key"}):
            with self.client(lambda request: httpx.Response(200, json={"results": RESULTS})), patch.object(
                GrokSearchProvider, "summarize", new_callable=AsyncMock,
                return_value="Summary.\n\nSources\n- https://fabricated.test/page"
            ):
                data = await server.web_search("runtime")
        cached = await server.get_sources(data["session_id"])
        self.assertIn("Summary.", data["content"])
        self.assertNotIn("fabricated.test", data["content"])
        self.assertEqual([s["url"] for s in cached["sources"]], [r["url"] for r in RESULTS])

    async def test_summarizer_receives_actual_snippets(self):
        provider = GrokSearchProvider("https://grok.test/v1", "test-key", "grok-model")
        sources = [{"url": r["url"], "title": r["title"], "description": r["content"],
                    "provider": "searxng"} for r in RESULTS]
        with patch.object(provider, "_execute_stream_with_retry", new_callable=AsyncMock, return_value="answer") as call:
            self.assertEqual(await provider.summarize("runtime", sources), "answer")
        headers, payload = call.call_args.args
        self.assertEqual(payload["model"], "grok-model")
        self.assertIn(json.dumps(sources[0], ensure_ascii=False), payload["messages"][1]["content"])
        self.assertIn("untrusted data", payload["messages"][0]["content"])

    async def test_original_grok_search_remains_available(self):
        with patch.dict(os.environ, {"SEARXNG_BASE_URL": "", "GROK_API_URL": "https://grok.test/v1",
                                    "GROK_API_KEY": "test-key"}), patch.object(
            GrokSearchProvider, "search", new_callable=AsyncMock,
            return_value="Original answer\n\nSources\n- https://example.org/original"
        ) as search:
            data = await server.web_search("runtime")
        search.assert_awaited_once_with("runtime", "")
        self.assertEqual(data["content"], "Original answer")
        self.assertEqual(data["sources_count"], 1)

    async def test_mcp_rejects_invalid_result_limit(self):
        async with Client(server.mcp) as client:
            with self.assertRaises(ToolError):
                await client.call_tool("web_search", {"query": "runtime", "extra_sources": -1})

    def test_config_supports_keyless_search(self):
        info = config.get_config_info()
        self.assertEqual(info["SEARCH_PROVIDER"], "searxng")
        self.assertEqual(info["SEARXNG_BASE_URL"], "http://searx.test")
        self.assertIn("搜索可用", info["config_status"])


if __name__ == "__main__":
    unittest.main()
