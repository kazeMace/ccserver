# PersonaChatGraph 执行流程

## 流程图

```mermaid
flowchart TD
    INPUT([用户输入\ncurrent_query / persona / history])

    web_search["**web_search**\nAgentNode\nplayground/web_search\n→ web_search_result"]

    topic_suggest["**topic_suggest**\nAgentNode\nplayground/topic_suggest\n→ topic_suggestion"]

    prepare_chat["**prepare_chat**\nFunctionNode\n组装 history_json + extra_context\n（含 topic_suggestion + reflection）"]

    chat_call["**chat_call**\nMCPToolNode\nchat-model MCP → conversation_chat\n→ chat_response"]

    quality_check["**quality_check**\nAgentNode\nroleplay_agent/.ccserver/agents/quality-check.md\n→ qc_raw"]

    parse_qc["**parse_qc**\nFunctionNode\n解析 JSON → passed / reflection"]

    OUTPUT([返回 chat_response])

    INPUT --> web_search
    web_search --> topic_suggest
    topic_suggest --> prepare_chat
    prepare_chat --> chat_call
    chat_call --> quality_check
    quality_check --> parse_qc

    parse_qc -->|passed=True| OUTPUT
    parse_qc -->|passed=False\n带 reflection 重试| prepare_chat
```

## 节点说明

| 节点 | 类型 | 来源 | 输出字段 |
|------|------|------|----------|
| `web_search` | AgentNode | `playground/web_search` | `web_search_result` |
| `topic_suggest` | AgentNode | `playground/topic_suggest / topic-suggest.md` | `topic_suggestion` |
| `prepare_chat` | FunctionNode | — | `history_json`, `extra_context` |
| `chat_call` | MCPToolNode | `chat-model` MCP → `conversation_chat` | `chat_response` |
| `quality_check` | AgentNode | `roleplay_agent/.ccserver/agents/quality-check.md` | `qc_raw` |
| `parse_qc` | FunctionNode | — | `passed`, `reflection` |

## 数据流

```
初始输入:
  current_query      用户消息
  persona            人设文本
  history_str        格式化历史（供 Agent 阅读）
  history_list       原始历史列表（供 prepare_chat 转 JSON）
  reflection         ""（初始为空，重试时由 parse_qc 填入）
  web_search_result  ""（初始为空）

重试时额外携带:
  reflection         quality_check 给出的具体修复建议
```
