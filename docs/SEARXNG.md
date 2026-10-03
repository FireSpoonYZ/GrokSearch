# SearXNG 搜索后端（此 fork）

设置 `SEARXNG_BASE_URL` 后，`web_search` 会先向 SearXNG 的 `/search?format=json` 检索，再让 Grok 根据实际标题、URL 和摘要整理回答。信源来自 SearXNG 返回的数据，按原有 `session_id` 缓存，`get_sources` 接口保持兼容。

原始结果也会随回答返回。Grok 未配置、摘要请求失败或设置 `SEARXNG_SUMMARIZE=false` 时仍可直接搜索。未设置 `SEARXNG_BASE_URL` 时使用 Grok Responses 原生搜索（接口需支持 `/responses` 和 `web_search` 工具）。修复说明见 [NATIVE_SEARCH.md](NATIVE_SEARCH.md)。

## 安装配置

SearXNG 需开启 JSON 输出（`search.formats` 包含 `json`）。服务必须能被 MCP 所在的电脑访问。

```json
{
  "mcpServers": {
    "grok-search": {
      "command": "uvx",
      "args": [
        "--python", "3.12",
        "--from", "git+https://github.com/FireSpoonYZ/GrokSearch@searxng-support",
        "grok-search"
      ],
      "env": {
        "SEARXNG_BASE_URL": "http://localhost:8080",
        "SEARXNG_SUMMARIZE": "true",
        "GROK_API_URL": "https://your-api-endpoint.example/v1",
        "GROK_API_KEY": "your-api-key",
        "GROK_MODEL": "your-grok-model",
        "NO_PROXY": "localhost,127.0.0.1"
      }
    }
  }
}
```

只需要搜索结果时可设 `SEARXNG_SUMMARIZE=false`，并省略三个 `GROK_*` 变量。内网 SearXNG 应将其主机名/IP 加入 `NO_PROXY`，避免 HTTP/HTTPS 代理转发内网请求。

| 变量 | 默认 | 作用 |
|---|---|---|
| `SEARXNG_BASE_URL` | 未配置 | SearXNG 实例根地址；设置后切换搜索后端 |
| `SEARXNG_ENGINES` | 实例默认 | 可选的逗号分隔引擎名，例如 `bing,github` |
| `SEARXNG_TIMEOUT` | `30` | SearXNG 请求超时秒数 |
| `SEARXNG_SUMMARIZE` | `true` | 是否调用 Grok 整理结果；没有 Grok 凭据时自动返回结果 |

SearXNG 模式中，`extra_sources` 是结果数量上限，`0` 表示默认 8 条，最大 50 条。`platform` 中的 GitHub、Reddit、Twitter/X 和 Hugging Face 会转换为对应的 `site:` 条件；多个平台用逗号分隔。其他平台名称会保留在 Grok 摘要请求中，但不自动限制站点。

## 返回结果与错误

除原有字段外，SearXNG 响应包含 `search_provider` 和 `unresponsive_engines`，便于区分“没有匹配结果”与“上游引擎限流/CAPTCHA”。连接、HTTP 和 JSON 格式错误会返回 `error=searxng_search_failed`，不降级为模型自行编造搜索结果。

Grok 摘要只依据搜索摘要，不代表已抓取或核验网页全文。SearXNG 的摘要也可能不完整，需要原始全文时另行抓取。`web_fetch` / `web_map` 仍使用上游 Tavily/Firecrawl：SearXNG 不提供这两种 API。

## 验证

```bash
uv venv --python 3.12
uv pip install -e '.[dev]'
python -m unittest discover -s tests -v
```

MCP 中调用 `web_search`，再用返回的 `session_id` 调用 `get_sources`；信源的 `provider` 应为 `searxng`。若返回空结果，检查 `unresponsive_engines`，或指定可用的 `SEARXNG_ENGINES`。修改 MCP 配置后需重载客户端。
