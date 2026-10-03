import json
import os
import unittest
from unittest.mock import AsyncMock, patch

import httpx

from grok_search import server
from grok_search.providers.grok import GrokSearchProvider
from grok_search.sources import split_answer_and_sources

RESPONSE = {
    "status": "completed",
    "output": [
        {"type": "web_search_call", "status": "completed"},
        {"type": "message", "content": [{
            "type": "output_text", "text": "Python homepage: https://www.python.org/.",
            "annotations": [
                {"type": "url_citation", "url": "https://www.python.org/", "title": "Python"},
                {"type": "url_citation", "url": "https://www.python.org/", "title": "Duplicate"},
            ],
        }]},
    ],
}


def sse(*events):
    return httpx.Response(200, content=(
        "\n\n".join("data: " + json.dumps(e) for e in events) + "\n\ndata: [DONE]\n"
    ).encode())


class GrokResponsesTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.provider = GrokSearchProvider("https://grok.test/v1/", "test-key", "grok-model")

    async def test_native_search_requests_real_web_search_tool(self):
        with patch.object(self.provider, "_execute_stream_with_retry", new_callable=AsyncMock, return_value="answer") as execute:
            self.assertEqual(await self.provider.search("Python", "GitHub"), "answer")
        args, kwargs = execute.call_args
        self.assertEqual(kwargs["endpoint"], "/responses")
        self.assertEqual(args[1]["tools"], [{"type": "web_search"}])
        self.assertEqual(args[1]["model"], "grok-model")
        self.assertIn("GitHub", args[1]["input"])

    async def test_response_stream_keeps_final_answer_and_actual_citations(self):
        body = sse(
            {"type": "response.created", "response": {"status": "in_progress"}},
            {"type": "response.output_text.delta", "delta": "partial"},
            {"type": "response.completed", "response": RESPONSE},
        )
        text = await self.provider._parse_streaming_response(body)
        answer, sources = split_answer_and_sources(text)
        self.assertEqual(answer, RESPONSE["output"][1]["content"][0]["text"])
        self.assertEqual(len(sources), 1)
        self.assertEqual(sources[0]["url"], "https://www.python.org/")

    async def test_response_json_keeps_citations(self):
        text = await self.provider._parse_streaming_response(httpx.Response(200, json=RESPONSE))
        self.assertEqual(split_answer_and_sources(text)[1][0]["url"], "https://www.python.org/")

    async def test_chat_stream_still_preserves_all_content(self):
        body = sse(
            {"choices": [{"delta": {"reasoning_content": "not final"}}]},
            {"choices": [{"delta": {"content": "Hello"}}]},
            {"choices": [{"delta": {"content": " world"}}]},
            {"choices": [{"delta": {}, "finish_reason": "stop"}]},
        )
        self.assertEqual(await self.provider._parse_streaming_response(body), "Hello world")

    async def test_interrupted_responses_stream_is_not_success(self):
        with self.assertRaisesRegex(ValueError, "response.completed"):
            await self.provider._parse_streaming_response(sse(
                {"type": "response.output_text.delta", "delta": "unfinished"}
            ))

    async def test_incomplete_responses_is_reported(self):
        with self.assertRaisesRegex(ValueError, "did not complete"):
            await self.provider._parse_streaming_response(sse(
                {"type": "response.incomplete", "response": {"status": "incomplete"}}
            ))

    async def test_native_failure_is_not_silently_swallowed(self):
        with patch.dict(os.environ, {"SEARXNG_BASE_URL": "", "GROK_API_URL": "https://grok.test/v1",
                                    "GROK_API_KEY": "test-key", "GUDA_API_KEY": ""}), patch.object(
            GrokSearchProvider, "search", new_callable=AsyncMock, side_effect=RuntimeError("unsupported endpoint")
        ):
            result = await server.web_search("Python")
        self.assertEqual(result["error"], "grok_search_failed")
        self.assertIn("unsupported endpoint", result["content"])

    async def test_unverified_plan_gets_warning(self):
        with patch.dict(os.environ, {"SEARXNG_BASE_URL": "", "GROK_API_URL": "https://grok.test/v1",
                                    "GROK_API_KEY": "test-key", "GUDA_API_KEY": ""}), patch.object(
            GrokSearchProvider, "search", new_callable=AsyncMock, return_value="I will search."
        ):
            result = await server.web_search("Python")
        self.assertEqual(result["sources_count"], 0)
        self.assertIn("warning", result)


if __name__ == "__main__":
    unittest.main()
