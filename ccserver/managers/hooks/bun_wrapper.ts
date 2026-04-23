/**
 * bun_wrapper.ts — ccserver bun 执行器的内置适配层
 *
 * 职责：
 *   1. 从 stdin 读取 ccserver 标准 JSON payload
 *   2. 转换为 OpenClaw 兼容的 event 对象（让用户的 handler.ts 可以直接用 OpenClaw API）
 *   3. 动态 import 用户的 handler 文件，调用指定的导出函数
 *   4. 把 handler 返回值和 event.messages 转换为 ccserver stdout JSON 格式输出
 *
 * 环境变量（由 ccserver 注入）：
 *   HOOK_SCRIPT  — 用户 handler 文件的绝对路径
 *   HOOK_EXPORT  — 要调用的导出函数名（默认 "default"）
 *
 * stdin/stdout 协议：
 *   stdin  — ccserver 标准 JSON payload（见设计文档第七节）
 *   stdout — ccserver 标准 JSON 输出（见设计文档第七节）
 *   exit code 2 — 阻断（block=true 时 bun_wrapper 自动以 exit 2 退出）
 *
 * 示例：用户 handler.ts 可以这样写（OpenClaw 风格）：
 *
 *   const handler = async (event) => {
 *     if (event.type === "tool" && event.context.toolName === "Bash") {
 *       event.messages.push("检测到 Bash 调用");
 *     }
 *   };
 *   export default handler;
 *
 * 也可以返回阻断（ccserver 风格）：
 *
 *   const handler = async (event) => {
 *     return { block: true, blockReason: "危险命令" };
 *   };
 *   export default handler;
 */

// ── 读取 stdin ────────────────────────────────────────────────────────────────

const stdinText = await Bun.stdin.text();

let payload: Record<string, unknown>;
try {
  payload = JSON.parse(stdinText);
} catch {
  // stdin 不是合法 JSON，静默退出（不阻断）
  process.exit(0);
}

// ── 构造 OpenClaw 兼容的 event 对象 ──────────────────────────────────────────
//
// OpenClaw 的 event 对象格式（用户 handler.ts 看到的）：
//   event.type       — 事件类型，如 "tool"（取 ccserver 标准名第一段）
//   event.action     — 具体动作，如 "call:before"（取第二段之后）
//   event.sessionKey — 对应 ccserver 的 session_id
//   event.timestamp  — 当前时间
//   event.messages   — string[]，handler 可以 push 内容，会转为 additionalContext
//   event.context    — 事件专属数据（工具名、输入、消息内容等）

const hookEventName = (payload.hook_event_name as string) || "";
const eventParts = hookEventName.split(":");
const eventType = eventParts[0] || "";             // 如 "tool"
const eventAction = eventParts.slice(1).join(":"); // 如 "call:before"

const event = {
  // 标准字段（OpenClaw 风格）
  type: eventType,
  action: eventAction,
  sessionKey: payload.session_id,
  timestamp: new Date(),
  messages: [] as string[],  // handler 可以 push 字符串，最终转为 additionalContext

  // 上下文字段（尽量覆盖 OpenClaw 常用字段）
  context: {
    // 通用字段
    workspaceDir: payload.project_root,
    agentId: payload.agent_id,
    agentName: payload.agent_name,
    depth: payload.depth,
    isOrchestrator: payload.is_orchestrator,

    // 工具相关（tool:call:before / tool:call:after）
    toolName: payload.tool_name,
    toolInput: payload.tool_input,
    toolResponse: payload.tool_response,
    toolError: payload.error,

    // 消息相关（message:inbound:received / message:inbound:claim）
    content: payload.prompt || payload.content,
    from: payload.from,
    channelId: payload.channel_id,
    senderId: payload.sender_id,
    conversationId: payload.conversation_id,
    messageId: payload.message_id,

    // 消息发送（message:outbound:sent）
    to: payload.to,
    success: payload.success,

    // LLM 输出（prompt:llm:output / agent:stop）
    reply: payload.reply,

    // 压缩相关（agent:compact:before / after）
    messageCount: payload.message_count,
    tokenCount: payload.token_count,
    compactedCount: payload.compacted_count,
    summaryLength: payload.summary_length,
    tokensBefore: payload.tokens_before,
    tokensAfter: payload.tokens_after,

    // 引导文件（agent:bootstrap）
    bootstrapFiles: payload.bootstrap_files,

    // 网关（gateway:startup）
    cfg: payload.cfg,
    deps: payload.deps,

    // 原始 payload（其他未映射的字段可以从这里取）
    _raw: payload,
  },
};

// ── 动态 import 用户 handler ──────────────────────────────────────────────────

const scriptPath = process.env.HOOK_SCRIPT;
const exportName = process.env.HOOK_EXPORT || "default";

if (!scriptPath) {
  // HOOK_SCRIPT 未设置，这是框架 bug，静默退出
  process.exit(0);
}

let handlerModule: Record<string, unknown>;
try {
  handlerModule = await import(scriptPath);
} catch (err) {
  // import 失败，打印到 stderr，以非阻断错误退出
  console.error(`bun_wrapper: failed to import ${scriptPath}: ${err}`);
  process.exit(1);
}

const handler = handlerModule[exportName];
if (typeof handler !== "function") {
  console.error(`bun_wrapper: export '${exportName}' is not a function in ${scriptPath}`);
  process.exit(1);
}

// ── 调用 handler ──────────────────────────────────────────────────────────────

let handlerResult: unknown = undefined;
try {
  handlerResult = await handler(event);
} catch (err) {
  // handler 抛出异常，非阻断错误
  console.error(`bun_wrapper: handler threw error: ${err}`);
  process.exit(1);
}

// ── 构造 stdout 输出 ──────────────────────────────────────────────────────────
//
// 优先处理 handler 的返回值，再处理 event.messages（OpenClaw 风格的输出方式）。
//
// handler 可以返回：
//   undefined / null    — 无操作
//   { block, blockReason } — 阻断
//   { additionalContext }  — 注入上下文
//   { updatedInput }       — 修改工具输入（tool:call:before 专用）
//   { handled }            — claiming 模式

// 检查 handler 是否返回了阻断
if (
  handlerResult !== null &&
  handlerResult !== undefined &&
  typeof handlerResult === "object"
) {
  const r = handlerResult as Record<string, unknown>;

  if (r.block === true) {
    // 阻断：以 exit 2 退出，blockReason 写到 stderr
    const reason = (r.blockReason as string) || (r.block_reason as string) || "blocked by hook";
    process.stderr.write(reason);
    process.exit(2);
  }
}

// 构造输出 JSON
const output: Record<string, unknown> = {};
const hookSpecificOutput: Record<string, unknown> = {};

// 处理 event.messages（OpenClaw 风格：push 字符串即通知用户）
if (event.messages.length > 0) {
  hookSpecificOutput.additionalContext = event.messages.join("\n");
}

// 处理 handler 返回值（ccserver 风格 + OpenClaw 部分字段）
if (
  handlerResult !== null &&
  handlerResult !== undefined &&
  typeof handlerResult === "object"
) {
  const r = handlerResult as Record<string, unknown>;

  // additionalContext（覆盖 event.messages，如果两者都有则合并）
  if (typeof r.additionalContext === "string" && r.additionalContext) {
    const existing = hookSpecificOutput.additionalContext as string | undefined;
    hookSpecificOutput.additionalContext = existing
      ? existing + "\n" + r.additionalContext
      : r.additionalContext;
  }

  // updatedInput（tool:call:before 专用）
  if (r.updatedInput && typeof r.updatedInput === "object") {
    hookSpecificOutput.updatedInput = r.updatedInput;
  }

  // continue: false（Stop hook 专用，阻止 Agent 停止）
  if (r.continue === false) {
    output.continue = false;
    if (typeof r.stopReason === "string") {
      output.stopReason = r.stopReason;
    }
  }

  // handled（claiming 模式）
  if (r.handled === true) {
    hookSpecificOutput.handled = true;
  }

  // systemMessage
  if (typeof r.systemMessage === "string" && r.systemMessage) {
    output.systemMessage = r.systemMessage;
  }
}

// 只有 hookSpecificOutput 有内容时才输出
if (Object.keys(hookSpecificOutput).length > 0) {
  output.hookSpecificOutput = hookSpecificOutput;
}

// 输出 JSON 到 stdout
if (Object.keys(output).length > 0) {
  console.log(JSON.stringify(output));
}

process.exit(0);
