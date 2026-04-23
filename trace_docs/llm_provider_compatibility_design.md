# LLM 多厂商兼容层设计计划

## Context

目前 `ccserver` 深度绑定 Anthropic SDK：
- `ModelAdapter` 抽象层仅有 `AnthropicAdapter` 实现 (`ccserver/model/anthropic_adapter.py`)
- Agent、Compactor、PromptLib、工具系统全部以 Anthropic 消息格式运转（`system` 为 text block 列表，`content` 包含 `type: "text"|"tool_use"|"tool_result"` blocks，schema 使用 `input_schema`）
- 存在多处硬编码引用：
  - `ccserver/managers/hooks/manager.py:1243` 直接 `from ccserver.model.anthropic_adapter import get_default_adapter`
  - `ccserver/builtins/tools/web_fetch.py` 和 `web_search.py` 直接依赖 `AsyncAnthropic`

目标是支持 Anthropic、OpenAI、OpenRouter、Ollama、LMStudio、OneAPI、字节火山等模型后端，同时**保持业务层代码不变**，降低新人上手和维护成本。

## Recommended Approach

**在 `ModelAdapter` 层做格式转换，业务层完全保留 Anthropic 内部格式。**

理由：
1. 当前 PromptLib、Agent、工具调度、消息存储全部围绕 Anthropic block 格式构建。改成中间格式会触发数十处重构，不符合 CLAUDE.md "代码新人快速上手" 的要求。
2. OpenAI 格式的生态最广（OpenRouter、Ollama、LMStudio、OneAPI、火山等均提供 OpenAI-compatible API）。只需实现一个 `OpenAIAdapter` + 翻译器，即可覆盖绝大多数厂商。
3. 新增厂商 = 新增一个 Adapter 文件，职责清晰，调试定位简单。

## New File Structure

```
ccserver/model/
├── adapter.py                  # 现有 — 不变
├── anthropic_adapter.py        # 现有 — 不变（单例逻辑保留）
├── openai_adapter.py           # 新增 — OpenAI 兼容 Adapter（OpenAI、OpenRouter、Ollama、LMStudio、OneAPI）
├── volcano_adapter.py          # 新增 — 火山方舟专用 Adapter（volcenginesdkruntime Ark）
├── translator.py               # 新增 — Anthropic <-> OpenAI 消息/Schema 转换
├── factory.py                  # 新增 — 运行时 Adapter 选择
└── __init__.py                 # 修改 — 导出工厂和新增的公开符号
```

## Detailed Design

### 0. 架构原则调整（回应用户反馈后的关键变化）

相比第一版方案，本次计划引入三项重要调整：
1. **内置工具配置化**：所有内置工具的初始化参数都可通过 `settings.json` 中的 `toolConfig` 统一配置。
2. **PromptLib 决定工具集**：`ToolManager` 不再硬编码加载内置工具，而是通过 `PromptLib.build_tools()` 钩子获取；这让不同 PromptLib 可以替换/裁剪/扩展工具集。
3. **WebFetch/WebSearch 解耦**：`BTWebFetch` 只依赖 `ModelAdapter`（不再依赖 `AsyncAnthropic`）；`BTWebSearch` 保留 Anthropic Beta 专用逻辑，但设计上为后续接入通用搜索 API 预留接口。
4. **火山方舟独立 Adapter**：虽然接口与 OpenAI 类似，但 `volcenginesdkruntime Ark` API 有差异，需要独立的 `VolcanoAdapter`。
5. **工具可自定义模型**：每个需要 LLM 的工具（如 WebFetch、WebSearch）都接收模型名参数，用户可在 PromptLib 的 `build_tools()` 中自定义使用的模型。

### 1. `ccserver/model/translator.py`

职责：把 Anthropic 格式的输入转成 OpenAI 格式请求参数，以及把 OpenAI 响应内容还原成 Anthropic 对象。

**消息转换（Anthropic -> OpenAI）：**
- `system: list[dict]`（text blocks）-> 提取文本拼接为单个字符串，转换为 `messages` 列表开头的 `{"role": "system", "content": text}`
- `content: [{"type": "text", "text": "..."}]` -> 直接取 `"..."` 作为 `content` 字符串
- `content: [{"type": "tool_use", "id": "...", "name": "...", "input": {...}}]`（assistant 消息）-> 转换为 assistant 消息的 `tool_calls` 数组，`arguments` 取 `json.dumps(input)`
- `content: [{"type": "tool_result", "tool_use_id": "...", "content": "..."}]`（user 消息）-> 转换为 `{"role": "tool", "tool_call_id": "...", "content": "..."}` 的独立消息

**Schema 转换（Anthropic -> OpenAI）：**
- `{"name": "...", "description": "...", "input_schema": {...}}` -> `{"type": "function", "function": {"name": "...", "description": "...", "parameters": {...}}}`

**响应转换（OpenAI -> Anthropic）：**
- `choice.message.content` -> `[{"type": "text", "text": "..."}]`
- `choice.message.tool_calls` -> `[{"type": "tool_use", "id": "...", "name": "...", "input": json.loads(arguments)}]`
- `finish_reason` 映射：
  - `"stop"` -> `"end_turn"`
  - `"tool_calls"` -> `"tool_use"`
  - `"length"` -> `"max_tokens"`
  - 其他 -> `None`

### 2. `ccserver/model/openai_adapter.py`

基于 `openai.AsyncOpenAI` 或兼容该接口的客户端（如 `volcenginesdkarkruntime.AsyncArk`）实现 `ModelAdapter` 接口。

核心组件：
- `OpenAIAdapter(ModelAdapter)`：封装兼容 OpenAI 接口的异步客户端，实现 `create()` 和 `stream()`
- `_Message`（dataclass）：模拟 Anthropic SDK 的 `Message` 对象，有 `.content` 和 `.stop_reason`
- `_TextBlock` / `_ToolUseBlock`（dataclass）：模拟 Anthropic 的 block 对象
- `OpenAIStreamWrapper`：流式包装器，必须提供 `text_stream` 异步生成器和 `get_final_message()` 协程

构造函数支持注入自定义客户端类（`client_class`），默认为 `openai.AsyncOpenAI`。火山方舟使用独立的 `VolcanoAdapter`（基于 `volcenginesdkruntime.Ark`），因为其 API 与 OpenAI 有差异。

**流式工具调用处理：**
OpenAI 的 `tool_calls` delta 是按 `index` 分片到达的。`OpenAIStreamWrapper` 需在迭代时：
1. 立即 yield 所有 `delta.content` 文本片段给前端
2. 按 `index` 累加 `delta.tool_calls` 的 `id`、`function.name`、`function.arguments`
3. 流结束时对累加的 `arguments` 字符串做 `json.loads()`，组装成完整的 `_ToolUseBlock`
4. `get_final_message()` 返回组装好的 `_Message`

构造函数签名示例：
```python
class OpenAIAdapter(ModelAdapter):
    def __init__(self, client: AsyncOpenAI):
        self._client = client
```

### 3. `ccserver/model/factory.py`

提供运行时根据配置选择 Adapter 的能力，保持简单、无动态加载：

```python
_PROVIDER_BUILDERS = {
    "anthropic": lambda cfg: get_anthropic_default(),
    "openai":    lambda cfg: OpenAIAdapter.from_env(),
    "openrouter": lambda cfg: OpenAIAdapter(base_url="https://openrouter.ai/api/v1", api_key=os.getenv("OPENROUTER_API_KEY")),
    "ollama":    lambda cfg: OpenAIAdapter(base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1"), api_key="ollama"),
    "lmstudio":  lambda cfg: OpenAIAdapter(base_url=os.getenv("LMSTUDIO_BASE_URL", "http://localhost:1234/v1"), api_key=""),
    "oneapi":    lambda cfg: OpenAIAdapter(base_url=os.getenv("ONEAPI_BASE_URL"), api_key=os.getenv("ONEAPI_API_KEY")),
    "volcano":   lambda cfg: VolcanoAdapter(api_key=os.getenv("ARK_API_KEY")),
    "generic":   lambda cfg: OpenAIAdapter(base_url=cfg["base_url"], api_key=cfg["api_key"]),
}

def get_adapter(provider: str | None = None, **config) -> ModelAdapter: ...
```

**OpenRouter、Ollama、OneAPI 支持说明：**
这三家均提供标准 OpenAI-compatible API，因此直接复用同一个 `OpenAIAdapter`，仅需配置不同的 `base_url` 和 `api_key`：
- **OpenRouter**: `base_url="https://openrouter.ai/api/v1"`, 认证通过 `OPENROUTER_API_KEY`
- **Ollama**: `base_url="http://localhost:11434/v1"`（默认本地）, 通常 `api_key="ollama"`
- **OneAPI**: 通过 `ONEAPI_BASE_URL` + `ONEAPI_API_KEY` 自定义

### 4. PromptLib 决定工具集（新增机制）

这是对内置工具设计的核心改进：让 PromptLib 控制 ToolManager 加载哪些工具，而非硬编码。

**`settings.json` 新增字段 `toolConfig`：**
```json
{
  "provider": "openai",
  "model": "gpt-4o",
  "providerConfig": {
    "baseUrl": "https://api.openai.com/v1",
    "apiKey": "sk-xxx"
  },
  "toolConfig": {
    "Bash": { "workdir": "{{session.workdir}}", "timeout": 300 },
    "WebFetch": { "model": "gpt-4o-mini" },
    "WebSearch": { "enabled": false }
  }
}
```

**`PromptLib.build_tools()` 钩子（新增）：**
在 `ccserver/prompts_lib/base.py` 的 `PromptLib` 基类中添加：

```python
def build_tools(
    self,
    session: Session,
    adapter: ModelAdapter,
    settings: ProjectSettings,
) -> dict[str, BuiltinTools]:
    """
    返回该 PromptLib 所管理的内置工具字典。
    在 factory.py 调用 AgentFactory.create_root() 时被调用。

    子类可覆盖此方法：
      1. 完全替换工具集（如某些 lib 不需要特定工具）
      2. 自定义工具初始化参数（如 WebFetch 使用不同 model）
      3. 注入额外的自定义工具

    默认实现：返回 ccserver 内置工具的默认集合。
    """
    from ccserver.builtins.tools import (
        BTBash, BTRead, BTWrite, BTEdit,
        BTGlob, BTGrep, BTCompact,
        BTTaskCreate, BTTaskUpdate, BTTaskGet, BTTaskList,
        BTAskUser,
    )
    # 基础工具（不需要 LLM client）
    tools = {
        "Bash": BTBash(session.workdir, settings),
        "Read": BTRead(session.workdir),
        "Write": BTWrite(session.workdir),
        "Edit": BTEdit(session.workdir),
        "Glob": BTGlob(session.workdir),
        "Grep": BTGrep(session.workdir),
        "Compact": BTCompact(),
        "TaskCreate": BTTaskCreate(task_manager),
        "TaskUpdate": BTTaskUpdate(task_manager),
        "TaskGet": BTTaskGet(task_manager),
        "TaskList": BTTaskList(task_manager),
        "AskUser": BTAskUser(),
    }

    # 需要 LLM client 的工具
    if adapter is not None:
        # BTWebFetch 可跨厂商运行（普通 LLM 调用）
        tools["WebFetch"] = BTWebFetch(adapter)
        # BTWebSearch 仅 Anthropic 支持
        if self._is_anthropic_adapter(adapter):
            tools["WebSearch"] = BTWebSearch(adapter)

    # Agent 工具动态注入
    from ccserver.builtins.tools import BTAgent
    tools["Agent"] = BTAgent(agent_catalog=session.agents.build_catalog())

    return tools
```

**`settings.py` 读取 `toolConfig`：**
在 `ProjectSettings._merge()` 中新增解析逻辑：
```python
tool_config = get_dict(project_data, "toolConfig") or get_dict(global_data, "toolConfig") or {}
# tool_config 结构：{"ToolName": {"param": value}, ...}
```

**`factory.py` 调用变化：**
```python
# 旧
tool_manager = ToolManager(session.project_root, session.tasks, settings, resolved_adapter._client)

# 新
tool_manager = ToolManager(
    session.project_root,
    session.tasks,
    settings,
    adapter=resolved_adapter,  # 传 adapter 而非 client
)
# 或者完全由 PromptLib 构建工具
tools = get_lib(lib_id).build_tools(session, resolved_adapter, settings)
```

**ToolManager 简化：**
由于工具集由 PromptLib 构建，ToolManager 其至仅负责"过滤"（根据 settings.permissions.deny 禁用）和"查询"，不再负责加载内置工具：

```python
class ToolManager:
    def __init__(self, workdir, task_manager, settings, tools: dict[str, BuiltinTools]):
        self.workdir = workdir
        self.task_manager = task_manager
        self.settings = settings
        self._tools = tools  # 由 PromptLib.build_tools() 构建后传入

    def get_all_tools(self) -> dict:
        return self._tools

    def get_enabled_tools(self) -> tuple:
        all_tools = self.get_all_tools()
        enabled = self.settings.filter_tools(all_tools)
        disabled = {k: v for k, v in all_tools.items() if k not in enabled}
        return enabled, disabled
```

### 5. `BTWebFetch` 和 `BTWebSearch` 重构（含模型自定义能力）

**工具设计目标**：每个需要 LLM 的工具都应该能被用户自定义使用的模型，且支持在 PromptLib 中注册/替换。

**`BTWebFetch` 可接收自定义模型**：
```python
class BTWebFetch(BuiltinTools):
    name = "WebFetch"
    description = "Fetch a web page and process it with an LLM..."
    params = {
        "url": ToolParam(type="string", description="..."),
        "prompt": ToolParam(type="string", description="..."),
    }

    def __init__(self, adapter: ModelAdapter, model: str = "claude-haiku-4-5-20251001"):
        self.adapter = adapter  # 任意 ModelAdapter
        self.model = model    # 用户可自定义模型

    async def run(self, url: str, prompt: str) -> ToolResult:
        markdown = await _fetch_as_markdown(url)
        # 使用自定义模型
        response = await self.adapter.create(
            model=self.model,
            messages=[{"role": "user", "content": f"..."}],
            max_tokens=4096,
        )
        # 提取 response.content[0].text
```

**`BTWebSearch` 保留 Anthropic 专用，但支持 Provider 检查**：
```python
class BTWebSearch(BuiltinTools):
    name = "WebSearch"
    description = "Web search using Anthropic..."
    params = {...}

    def __init__(self, adapter: ModelAdapter, model: str = "claude-haiku-4-5-20251001"):
        self.adapter = adapter
        self.model = model

    async def run(self, query: str, ...) -> ToolResult:
        if not isinstance(self.adapter, AnthropicAdapter):
            return ToolResult.error(
                "WebSearch requires Anthropic provider."
            )
        # Anthropic Beta API 调用...
```

**PromptLib 中自定义工具和模型的方式**：
在 `build_tools()` 中，用户可以：
```python
def build_tools(self, session, adapter, settings):
    # 方式 1: 使用默认模型
    tools["WebFetch"] = BTWebFetch(adapter)

    # 方式 2: 自定义模型（替换默认模型）
    tools["WebFetch"] = BTWebFetch(adapter, model="gpt-4o-mini")

    # 方式 3: 完全替换工具实现
    from my_custom_tools import MySearchTool
    tools["WebSearch"] = MySearchTool(adapter, model="my-model")
```

这样，不同的 PromptLib 可以使用不同的模型，实现完全的定制能力。

配置来源优先级（从高到低），与现有 `ProjectSettings` 的权限/运行模式等配置保持一致：
1. 代码运行时显式传入的参数（`adapter` 构造函数参数）
2. `ProjectSettings` 项目级配置（`<project>/.ccserver/settings.local.json`）
3. `ProjectSettings` 全局级配置（`~/.ccserver/settings.json`）
4. 环境变量 `CCSERVER_PROVIDER`、`CCSERVER_MODEL`、`CCSERVER_BASE_URL`、`CCSERVER_API_KEY`
5. 默认值 `anthropic`

各厂商的标准环境变量约定：
- Anthropic: `ANTHROPIC_API_KEY`
- OpenAI: `OPENAI_API_KEY`
- OpenRouter: `OPENROUTER_API_KEY`
- 火山方舟 (volcengine-python-sdk[ark]): `ARK_API_KEY` + `ARK_BASE_URL`
- Ollama: `OLLAMA_BASE_URL`
- LMStudio: `LMSTUDIO_BASE_URL`

**火山方舟 Adapter**：
用户提到的 `volcengine-python-sdk[ark]`（火山方舟 Ark API）虽然名字类似 OpenAI，但存在以下差异：
1. 认证方式：Ark 使用 `api_key` 配合 `base_url`，但模型 ID 格式不同（如 `doubao-seed-1-6-251015`）
2. API 端点：`base_url` 固定为 `https://ark.cn-beijing.volces.com/api/v3`
3. SDK 类名：`volcenginesdkarkruntime.Ark` 与 `openai.AsyncOpenAI` 不完全兼容

因**立独立的 `VolcanoAdapter`**（`ccserver/model/volcano_adapter.py`），基于 `volcenginesdkruntime.Ark` 实现 `ModelAdapter` 接口。`OpenAIAdapter` 仅处理标准的 OpenAI-compatible 后端。

**`settings.json` 新增字段示例：**
```json
{
  "provider": "openai",
  "model": "gpt-4o",
  "providerConfig": {
    "baseUrl": "https://api.openai.com/v1",
    "apiKey": "sk-xxx"
  }
}
```
合并规则与现有配置一致：项目 `settings.local.json` 覆盖全局 `settings.json`，都不存在则 fallback 到环境变量。

### 5. 修改已有文件

| 文件 | 修改内容 |
|------|----------|
| `ccserver/model/__init__.py` | 导出 `OpenAIAdapter`、`get_adapter`；保留 `get_default_adapter` 作为向后兼容别名（内部调用 `get_adapter("anthropic")`） |
| `ccserver/config.py` | 新增 `PROVIDER = os.getenv("CCSERVER_PROVIDER", "anthropic")`、`CCSERVER_BASE_URL`、`CCSERVER_API_KEY` |
| `ccserver/settings.py` | `ProjectSettings` 新增 `provider`, `model`, `provider_config`, `tool_config` 字段读取与合并逻辑 |
| `ccserver/prompts_lib/base.py` | `PromptLib` 新增 `build_tools()` 钩子方法 |
| `ccserver/prompts_lib/cc_reverse/v2_1_81/lib.py` | 覆盖 `build_tools()` 返回默认工具集（包含 WebFetch/WebSearch） |
| `ccserver/managers/hooks/manager.py:1243-1244` | 将 `from ccserver.model.anthropic_adapter import get_default_adapter` + `get_default_adapter()` 替换为 `from ccserver.model import get_adapter` + `get_adapter()` |
| `ccserver/factory.py` | 1. `AgentFactory.create_root()` 改为调用 PromptLib 的 `build_tools()` 获取工具集；<br>2. `ToolManager` 构造函数改为接收 `tools` 字典而非自行加载 |
| `ccserver/managers/tools/manager.py` | 简化为仅负责过滤，不负责加载 |
| `ccserver/builtins/tools/web_fetch.py` | 改为接收 `ModelAdapter` 而非 `AsyncAnthropic` |
| `ccserver/builtins/tools/web_search.py` | 添加 provider 检查逻辑 |
| `ccserver/main.py` | `AgentRunner` 不再提前绑定全局 `adapter`；在 `run()` 中根据 `session.settings` 惰性解析 |
| `ccserver/pipeline/graph.py` | 检查 `get_default_adapter()` 调用，统一替换为 `get_adapter()` |
| `playground/graphs/simple_roleplay_graph/graph.py` | 同上 |

### 6. 硬编码点说明

**`BTWebFetch` 和 `BTWebSearch` 现在改为**：
- `BTWebFetch` 接收 `ModelAdapter`（任意后端可运行）
- `BTWebSearch` 保留 Anthropic Beta 专用逻辑，但添加 provider 检查

**后续可扩展性**：
- 后续可考虑将 WebSearch 替换为第三方搜索 API（SerpAPI、Tavily），只需在自定义 PromptLib 的 `build_tools()` 中使用自定义搜索工具类覆盖默认的 WebSearch。

## Edge Cases

1. **System prompt 格式**：Anthropic 支持 `system: list[dict]` 或 `str`，OpenAI 只支持单条 system message 字符串。转换时需拼接所有 system text blocks，用换行分隔。
2. **Tool result 内容类型**：Anthropic 的 `tool_result.content` 可为 `str` 或 `list[dict]`（含图片）。OpenAI `role="tool"` 只接受字符串。转换时若遇到非字符串内容，使用 `str()` 或 base64 序列化降级为字符串。
3. **温度参数**：`AnthropicAdapter` 通过 `**kwargs` 透传。`OpenAIAdapter` 同样透传 `**kwargs` 给 `chat.completions.create(...)`。
4. **超时与连接复用**：参考 `AnthropicAdapter` 的做法，为 `OpenAIAdapter` 配置自定义 `httpx.AsyncClient`，设置 `timeout=600s` 和 `keepalive_expiry=5`，避免 MCP 长调用后连接被服务端关闭导致的 chunk read 错误。
5. **模型名**：`config.MODEL` 继续使用，用户通过 `CCSERVER_MODEL` 指定。适配器工厂不负责模型名映射，由用户自行配置正确的厂商模型 ID。
6. **AgentRunner 向后兼容**：`AgentRunner` 仍然接受 `__init__(model=..., adapter=...)`，若显式传入则优先使用；未传入时再从 `session.settings` 读取。这样已有的 `server.py` 和 `tui.py` 实例化方式无需修改。
7. **PromptLib.build_tools() 调用时机**：`build_tools()` 需要 `session` 和 `adapter`，因此在 `factory.py` 中先创建 adapter 再构建工具集。工具集构建依赖 session 对象（workdir、agents 等），顺序不能颠倒。
8. **多个 PromptLib 共存**：每个 PromptLib 可定义自己的 `build_tools()`，通过 `prompt_version` 选择使用哪个 lib 版本，即选择对应的工具集实现。

## Verification Plan

1. **单元测试**：新建 `tests/test_openai_adapter.py`，覆盖以下场景：
   - `translator.py`：system blocks 拼接、text block 提取、tool_use -> tool_calls、tool_result -> tool message、schema `input_schema` -> `parameters`、反向响应映射
   - `OpenAIStreamWrapper`：纯文本流、带 tool_calls 的流、分片 arguments 的正确拼接与 JSON 解析
   - `factory.py`：各 provider 字符串的 dispatch 逻辑、未知 provider 报错

2. **集成验证**：
   - 设置 `CCSERVER_PROVIDER=ollama`、`CCSERVER_MODEL=llama3.1`、启动本地 Ollama
   - 启动 server，通过 API 发送一条简单对话，验证 Agent 主循环正常返回
   - 触发一次带工具调用的对话（如 Bash），验证 tool_use 请求和 tool_result 返回均正确

3. **回归测试**：
   - `CCSERVER_PROVIDER=anthropic`（默认）下，执行全部现有 pytest，确保 100% 通过
