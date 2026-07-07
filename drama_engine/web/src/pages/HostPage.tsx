// 主持人页：生命周期控制 + 圆桌 + 消息流 + 只读视图 + 回滚面板。
// host seat 拿上帝视角 inbox（含 host-only 元事件）。moderator 操作在 v1 端点就绪后接入。

import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { getClient, type RollbackPoint } from "../api/client";
import { Topbar, ConnStatus } from "../components/Chrome";
import { MessageFeed } from "../components/MessageFeed";
import { RoundTable } from "../components/RoundTable";
import { Sidebar } from "../components/Sidebar";
import { useInbox } from "../hooks/useInbox";
import { useStateView } from "../hooks/useStateView";

export function HostPage() {
  const { sessionId = "" } = useParams();
  const inbox = useInbox(sessionId, "host");
  const view = useStateView(sessionId, "host");
  const [busy, setBusy] = useState(false);
  const [points, setPoints] = useState<RollbackPoint[]>([]);

  const run = async (fn: () => Promise<void>) => {
    setBusy(true);
    try {
      await fn();
    } catch (e) {
      alert(String(e));
    } finally {
      setBusy(false);
    }
  };

  const client = getClient();
  const lastSpeech = [...inbox.messages].reverse().find((m) => m.role === "dialogue") ?? null;

  const loadPoints = async () => setPoints(await client.rollbackPoints(sessionId));
  useEffect(() => {
    loadPoints().catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId]);

  return (
    <div className="host-shell">
      <Topbar
        title="主持人控制台"
        sub={sessionId}
        phase={inbox.phase}
        right={<ConnStatus status={inbox.status} />}
      />
      <div className="host-body">
        {/* 左栏：控制 */}
        <div className="host-left">
          <div>
            <div className="host-section-title">生命周期</div>
            <div className="ctrl-group">
              <div className="ctrl-row">
                <button className="btn" disabled={busy} onClick={() => run(() => client.assign(sessionId))}>发牌</button>
                <button className="btn primary" disabled={busy} onClick={() => run(() => client.start(sessionId))}>开始</button>
              </div>
              <div className="ctrl-row">
                <button className="btn" disabled={busy} onClick={() => run(() => client.pause(sessionId))}>暂停</button>
                <button className="btn" disabled={busy} onClick={() => run(() => client.resume(sessionId))}>继续</button>
              </div>
              <div className="ctrl-row">
                <button className="btn" disabled={busy} onClick={() => run(() => client.step(sessionId, 1))}>单步</button>
                <button className="btn" disabled={busy} onClick={() => run(() => client.restart(sessionId))}>重开</button>
              </div>
              <button className="btn danger" disabled={busy} onClick={() => run(() => client.terminate(sessionId))}>终止对局</button>
            </div>
          </div>

          <div>
            <div className="host-section-title">回滚 / Checkpoint</div>
            <div className="ctrl-row">
              <button className="btn sm" disabled={busy} onClick={() => run(async () => { await client.checkpoint(sessionId, "manual"); await loadPoints(); })}>建立检查点</button>
              <button className="btn sm" disabled={busy} onClick={() => run(loadPoints)}>刷新</button>
            </div>
            <div style={{ marginTop: 8 }}>
              {points.length === 0 ? <div className="empty-hint" style={{ padding: 12 }}>暂无检查点</div> : null}
              {points.map((p) => (
                <div className="ckpt-item" key={p.checkpoint_id}>
                  <span className="cid">{p.checkpoint_id}</span>
                  <span className="creason">{p.reason}</span>
                  <button className="btn sm" disabled={busy} onClick={() => run(async () => { await client.rollbackTo(sessionId, p.checkpoint_id); await loadPoints(); })}>回滚</button>
                </div>
              ))}
            </div>
          </div>
        </div>

        {/* 中栏：圆桌 + 消息流 */}
        <div className="host-center">
          {view?.players?.length ? <RoundTable players={view.players} lastSpeech={lastSpeech} /> : null}
          <MessageFeed messages={inbox.messages} />
        </div>

        {/* 右栏：状态面板（复用 Sidebar 的内容以只读呈现） */}
        <div className="host-right">
          <Sidebar view={view} />
        </div>
      </div>
    </div>
  );
}
