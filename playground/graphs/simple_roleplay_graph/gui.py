"""
gui — Gradio 测试界面，通过 HTTP 接口调用 SimpleRoleplayGraph server。

先启动 server：
    python playground/graphs/simple_roleplay_graph/main.py

再运行 gui：
    python playground/graphs/simple_roleplay_graph/gui.py
"""

import sys
from pathlib import Path

import gradio as gr
import httpx

# ── 配置 ──────────────────────────────────────────────────────────────────────

SERVER_URL = "http://localhost:8001"

# ── 事件处理 ──────────────────────────────────────────────────────────────────


def fetch_personas() -> list[str]:
    try:
        resp = httpx.get(f"{SERVER_URL}/personas", timeout=5)
        return resp.json().get("personas", [])
    except Exception:
        return []


def new_session(persona_id: str):
    """POST /sessions 创建新 session。"""
    try:
        resp = httpx.post(
            f"{SERVER_URL}/sessions",
            json={"persona_id": persona_id},
            timeout=5,
        )
        session_id = resp.json()["session_id"]
    except Exception as e:
        return "", [], f"[错误] 创建 session 失败: {e}"
    return session_id, [], f"已创建 session: {session_id[:8]}..."


async def send_message(message: str, persona_id: str, session_id: str, history: list):
    """POST /chat 发送消息。"""
    if not message.strip():
        yield history, ""
        return

    if not session_id:
        session_id, history, _ = new_session(persona_id)

    history = history + [{"role": "user", "content": message}]
    yield history, ""

    try:
        async with httpx.AsyncClient(timeout=300) as client:
            resp = await client.post(
                f"{SERVER_URL}/chat",
                json={"message": message, "persona_id": persona_id},
                headers={"X-Session-Id": session_id},
            )
        resp.raise_for_status()
        reply = resp.json()["reply"]
    except Exception as e:
        reply = f"[错误] {e}"

    history = history + [{"role": "assistant", "content": reply}]
    yield history, ""


# ── 界面 ──────────────────────────────────────────────────────────────────────

personas = fetch_personas()
default_persona = personas[0] if personas else ""

with gr.Blocks(title="SimpleRoleplayGraph 测试") as demo:
    gr.Markdown("# SimpleRoleplayGraph 测试界面")

    with gr.Row():
        with gr.Column(scale=1):
            persona_dd = gr.Dropdown(
                choices=personas,
                value=default_persona,
                label="人设",
            )
            new_btn = gr.Button("新建会话", variant="secondary")
            session_label = gr.Textbox(
                label="当前 Session",
                value="（未创建）",
                interactive=False,
            )
            session_id_state = gr.State("")

        with gr.Column(scale=3):
            chatbot = gr.Chatbot(label="对话", height=500)
            with gr.Row():
                msg_input = gr.Textbox(
                    placeholder="输入消息...",
                    show_label=False,
                    scale=4,
                )
                send_btn = gr.Button("发送", variant="primary", scale=1)

    # ── 事件绑定 ──────────────────────────────────────────────────────────────

    new_btn.click(
        fn=new_session,
        inputs=[persona_dd],
        outputs=[session_id_state, chatbot, session_label],
    )

    send_btn.click(
        fn=send_message,
        inputs=[msg_input, persona_dd, session_id_state, chatbot],
        outputs=[chatbot, msg_input],
    )

    msg_input.submit(
        fn=send_message,
        inputs=[msg_input, persona_dd, session_id_state, chatbot],
        outputs=[chatbot, msg_input],
    )

if __name__ == "__main__":
    demo.launch(server_port=7860, show_error=True)
