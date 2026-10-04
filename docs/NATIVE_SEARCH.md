# 原生搜索输出异常：原因与修复

## 复现结果

旧版 `GrokSearchProvider.search()` 向 `/chat/completions` 发送了模型、system/user 消息和 `stream=true`，没有 `tools`，也没有执行工具回合。提示词要求检索，不能替代 API 的实际工具配置。

在测试的 OpenAI 兼容接口上，旧请求返回 HTTP 200、`finish_reason=stop`，正文只有“先去检索”的计划，没有工具调用或信源。完整 SSE 正文与旧解析器输出一致，因此不是流被截断，也不是等待 `get_sources` 能解决的问题。`get_sources` 只是查询已完成搜索的缓存。

对照请求：

- 为 Chat Completions 显式提供搜索函数后，接口返回了 `tool_calls`。调用方仍需执行函数并继续工具回合，不能把它当作最终答案。
- 使用 `/responses` 与 `tools: [{"type": "web_search"}]` 后，接口执行了 `web_search_call`，返回最终答案及 URL 引用。

缺少 Tavily/Firecrawl Key 导致 `web_fetch`/`web_map` 不可用，是另一个问题。它们在旧版搜索里是可选补充，不是上述空信源的直接原因。

## 此 fork 的修复

未配置 `SEARXNG_BASE_URL` 时，原生搜索使用：

```json
{
  "model": "your-grok-model",
  "input": "Search query",
  "tools": [{"type": "web_search"}],
  "stream": true
}
```

请求端点为 `/responses`。解析器支持 Responses 的文本事件及 `response.completed`，从最终消息的 `url_citation` annotations 提取真实引用，供 `get_sources` 返回。摘要和其他普通生成仍使用 Chat Completions。

- 搜索接口失败会返回 `error=grok_search_failed`，不再悄悄吞掉异常。
- Responses 流未完成或返回 incomplete/failed 时按错误处理。
- 没有可验证信源的原生回答会带 `warning`，不能作为已联网的证明。
- SSE Chat Completions 解析仍保持兼容。

接口必须支持 Responses 原生搜索工具。只支持普通 Chat Completions 的代理可以改用 [SearXNG 后端](SEARXNG.md)，由本 MCP 获取真实搜索结果，再交给模型整理。

## 独立的 X/Twitter 搜索

此 fork 提供两个独立 MCP 工具，内部共享模型校验、Responses 请求、引用解析与信源缓存：

| MCP 工具 | 上游工具 | 检索范围 |
|---|---|---|
| `web_search(query, platform, model, extra_sources)` | `web_search` | 公共网页；配置 SearXNG 时使用 SearXNG |
| `x_search(query, model)` | `x_search` | X/Twitter 公开帖子与账号，始终使用 Grok 原生搜索 |

每次原生请求只声明选定的工具，不自动同时启用两种搜索。需要综合网页与 X 讨论时，调用方可分别调用两个 MCP 工具。`web_search(platform="Twitter")` 只会让网页查询聚焦该平台，不会切换为原生 X 搜索。

例如：

```text
x_search(query="查找 @OpenAI 最近两条公开帖子，给出日期和直接帖子链接")
get_sources(session_id="上一步返回的 session_id")
```

`x_search` 使用配置的默认模型，除非明确传入 `model`。返回字段与原生网页搜索一致：`session_id`、`content`、`sources_count`，另有 `search_tool=x_search`。即使配置了 SearXNG，它也不会将 X 查询转成网页搜索。上游不支持 X 搜索时返回明确错误。

## 验证范围

新增测试覆盖：请求端点与工具声明、Responses 流式/非流式引用、未完成流、普通 Chat Completions 兼容、错误与无信源警告。真实 MCP stdio 测试中，修复后的原生搜索返回了 Python 官方首页及引用，`get_sources` 能取回信源。

这些结果验证了搜索流程，没有评估模型在复杂研究任务上的结论准确性。
