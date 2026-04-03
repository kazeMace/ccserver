#!/usr/bin/env python3
"""
CCServer GUI — 基于 Gradio 的单文件图形界面，通过 HTTP 接口与后端通信。
需要先启动 server.py 服务。

用法：
    python clients/gui.py
    python clients/gui.py --server-port 7860
"""

import httpx
import gradio as gr

# ─── API 调用 ─────────────────────────────────────────────────────────────────


def api_create_session(base_url: str) -> dict:
    resp = httpx.post(f"{base_url}/sessions", json={}, timeout=10)
    resp.raise_for_status()
    return resp.json()


def api_list_sessions(base_url: str) -> list:
    resp = httpx.get(f"{base_url}/sessions", timeout=10)
    resp.raise_for_status()
    return resp.json()


def api_get_session(base_url: str, session_id: str) -> dict | None:
    resp = httpx.get(f"{base_url}/sessions/{session_id}", timeout=10)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    return resp.json()


def api_chat(base_url: str, session_id: str, message: str) -> dict:
    headers = {"X-Session-Id": session_id} if session_id else {}
    resp = httpx.post(
        f"{base_url}/chat",
        json={"message": message},
        headers=headers,
        timeout=900,
    )
    resp.raise_for_status()
    return resp.json()


# ─── 工具函数 ─────────────────────────────────────────────────────────────────


def format_session_label(session: dict) -> str:
    """把 session dict 格式化成列表中显示的文字。"""
    sid = session["id"][:8]
    updated = session.get("updated_at", "")[:16].replace("T", " ")
    return f"{sid}  {updated}"


def sessions_to_choices(sessions: list) -> list[str]:
    return [format_session_label(s) for s in sessions]


def find_session_id_by_label(sessions: list, label: str) -> str | None:
    """通过显示标签反查完整 session_id。"""
    for s in sessions:
        if format_session_label(s) == label:
            return s["id"]
    return None


# ─── 事件处理 ─────────────────────────────────────────────────────────────────


def connect(base_url: str):
    """点击「连接」按钮：验证后端可达，拉取 session 列表，新建一个默认 session。"""
    base_url = base_url.rstrip("/")
    try:
        sessions = api_list_sessions(base_url)
    except Exception as e:
        return (
            gr.update(value=f"❌ 无法连接: {e}", visible=True),  # status
            gr.update(choices=[], value=None),                    # session_list
            None,                                                 # session_id state
            [],                                                   # sessions_cache state
            base_url,                                             # base_url state
            [],                                                   # chatbot
        )

    # 新建一个 session 作为当前会话
    try:
        new_session = api_create_session(base_url)
        sessions.insert(0, new_session)
        current_id = new_session["id"]
    except Exception:
        current_id = sessions[0]["id"] if sessions else None

    choices = sessions_to_choices(sessions)
    current_label = choices[0] if choices else None

    return (
        gr.update(value=f"✅ 已连接到 {base_url}", visible=True),
        gr.update(choices=choices, value=current_label),
        current_id,
        sessions,
        base_url,
        [],
    )


def new_session(base_url: str, sessions_cache: list):
    """新建 session 并切换到它。"""
    if not base_url:
        return gr.update(), None, sessions_cache, []

    try:
        session = api_create_session(base_url)
    except Exception as e:
        return gr.update(), None, sessions_cache, []

    sessions_cache.insert(0, session)
    choices = sessions_to_choices(sessions_cache)
    new_label = choices[0]

    return (
        gr.update(choices=choices, value=new_label),
        session["id"],
        sessions_cache,
        [],  # 清空对话框
    )


def switch_session(label: str, sessions_cache: list, base_url: str):
    """点击 session 列表项，切换当前会话并加载历史消息。"""
    session_id = find_session_id_by_label(sessions_cache, label)
    if not session_id or not base_url:
        return session_id, []

    # 从后端获取消息数量（暂不拉取完整消息内容，仅作切换）
    # 如果后端将来支持拉取消息列表，可在此处填充 history
    return session_id, []


def user_submit(user_input: str, history: list):
    """立即把用户消息追加到 chatbot，清空输入框，然后触发 bot_respond。"""
    if not user_input.strip():
        return history, ""
    history = history + [{"role": "user", "content": user_input}]
    return history, ""


def bot_respond(history: list, base_url: str, session_id: str):
    """用户消息已在 history 末尾，向后端请求回复并追加。"""
    if not history:
        return history, session_id

    last = history[-1]
    if last["role"] != "user":
        return history, session_id

    # Gradio 新版 type="messages" 时，content 可能是 list[{"type":"text","text":"..."}]
    raw_content = last["content"]
    if isinstance(raw_content, list):
        user_input = " ".join(
            part["text"] for part in raw_content if isinstance(part, dict) and "text" in part
        )
    else:
        user_input = str(raw_content)

    if not base_url or not session_id:
        history = history + [{"role": "assistant", "content": "❌ 请先连接到后端并选择 session。"}]
        return history, session_id

    try:
        result = api_chat(base_url, session_id, user_input)
        reply = result.get("reply", "（无回复）")
        new_sid = result.get("session_id", session_id)
    except httpx.HTTPStatusError as e:
        reply = f"❌ HTTP {e.response.status_code}: {e.response.text}"
        new_sid = session_id
    except Exception as e:
        reply = f"❌ 请求失败: {e}"
        new_sid = session_id

    history = history + [{"role": "assistant", "content": reply}]
    return history, new_sid


# ─── 界面构建 ─────────────────────────────────────────────────────────────────


def build_ui():
    with gr.Blocks(title="CCServer Chat") as demo:

        # ── State ──────────────────────────────────────────────────────────────
        state_session_id = gr.State(None)
        state_sessions_cache = gr.State([])
        state_base_url = gr.State("http://localhost:8000")

        # ── 顶部：连接配置 ──────────────────────────────────────────────────────
        gr.Markdown("## 🤖 CCServer Chat")
        with gr.Row():
            input_url = gr.Textbox(
                value="http://localhost:8000",
                label="后端地址",
                placeholder="http://localhost:8000",
                scale=4,
            )
            btn_connect = gr.Button("连接", variant="primary", scale=1)
        status_text = gr.Markdown(visible=False)

        # ── 主体：左侧 session 列表 + 右侧对话区 ──────────────────────────────
        with gr.Row(equal_height=True):

            # 左侧
            with gr.Column(scale=1, min_width=200):
                gr.Markdown("### Sessions")
                session_list = gr.Radio(
                    choices=[],
                    label="",
                    interactive=True,
                    elem_id="session-list",
                )
                btn_new = gr.Button("➕ 新建会话", variant="secondary")
                gr.HTML("""
                <style>
                #session-list {
                    max-height: 420px;
                    overflow-y: auto;
                }
                </style>
                """)

            # 右侧
            with gr.Column(scale=3):
                chatbot = gr.Chatbot(
                    label="对话",
                    height=500,
                    render_markdown=True,
                )
                with gr.Row():
                    input_msg = gr.Textbox(
                        placeholder="输入消息，Enter 发送",
                        label="",
                        lines=1,
                        scale=5,
                    )
                    btn_send = gr.Button("发送", variant="primary", scale=1)

        # ── 事件绑定 ───────────────────────────────────────────────────────────

        btn_connect.click(
            fn=connect,
            inputs=[input_url],
            outputs=[
                status_text,
                session_list,
                state_session_id,
                state_sessions_cache,
                state_base_url,
                chatbot,
            ],
        )

        btn_new.click(
            fn=new_session,
            inputs=[state_base_url, state_sessions_cache],
            outputs=[session_list, state_session_id, state_sessions_cache, chatbot],
        )

        session_list.change(
            fn=switch_session,
            inputs=[session_list, state_sessions_cache, state_base_url],
            outputs=[state_session_id, chatbot],
        )

        # 第一步：立即显示用户消息、清空输入框
        # 第二步：请求后端并显示回复
        input_msg.submit(
            fn=user_submit,
            inputs=[input_msg, chatbot],
            outputs=[chatbot, input_msg],
        ).then(
            fn=bot_respond,
            inputs=[chatbot, state_base_url, state_session_id],
            outputs=[chatbot, state_session_id],
        )

        btn_send.click(
            fn=user_submit,
            inputs=[input_msg, chatbot],
            outputs=[chatbot, input_msg],
        ).then(
            fn=bot_respond,
            inputs=[chatbot, state_base_url, state_session_id],
            outputs=[chatbot, state_session_id],
        )

    return demo


# ─── 入口 ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="CCServer Gradio GUI")
    parser.add_argument("--server-port", type=int, default=7860)
    parser.add_argument("--server-name", type=str, default="127.0.0.1")
    parser.add_argument("--share", action="store_true")
    args = parser.parse_args()

    demo = build_ui()
    demo.launch(
        server_name=args.server_name,
        server_port=args.server_port,
        share=args.share,
        theme=gr.themes.Soft(),
    )
