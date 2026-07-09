// 主持人页：生命周期控制 + 圆桌 + 消息流 + 只读视图 + 回滚面板。
// host seat 拿上帝视角 inbox（含 host-only 元事件）。moderator 操作在 v1 端点就绪后接入。

import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { getClient, type RollbackPoint } from "../api/client";
import { GAMES } from "../api/mockData";
import { inferGameKind, themeGenreForKind } from "../api/gamePresentation";
import { ImmersiveShell, type RailItem } from "../components/AppShell";
import { Topbar, Channels, ConnStatus } from "../components/Chrome";
import { MessageFeed } from "../components/MessageFeed";
import { RoundTable } from "../components/RoundTable";
import { Sidebar } from "../components/Sidebar";
import { useInbox } from "../hooks/useInbox";
import { useStateView } from "../hooks/useStateView";
import { useChannels } from "../hooks/useChannels";

export function HostPage() {
  const { sessionId = "" } = useParams();
  const inbox = useInbox(sessionId, "host");
  const view = useStateView(sessionId, "host");
  const [busy, setBusy] = useState(false);
  const [points, setPoints] = useState<RollbackPoint[]>([]);
  const [activeChannel, setActiveChannel] = useState("public");
  const [sessionGameHint, setSessionGameHint] = useState("");
  // host 是上帝视角，能看到全部 scope（公开/狼人/预言家…），频道条便于分频道查看。
  const scopeLabels = (view?.panels?.scope_labels as Record<string, [string, string]>) ?? {};
  const { channels, filter } = useChannels(inbox.messages, scopeLabels);
  useEffect(() => {
    if (!channels.some((c) => c.id === activeChannel)) setActiveChannel("public");
  }, [channels, activeChannel]);

  const run = async (fn: () => Promise<void>) => {
    setBusy(true);
    try {
      await fn();
      await inbox.refresh();
    } catch (e: any) {
      // 409 是状态冲突（已发牌/已运行等），静默处理
      if (e?.status !== 409) alert(String(e));
    } finally {
      setBusy(false);
    }
  };

  const client = getClient();
  const lastSpeech = [...inbox.messages].reverse().find((m) => m.role === "dialogue") ?? null;
  const genre = inferGameKind(view?.panels?.genre as string) ?? inferGameKind(sessionGameHint) ?? inferGameKind(sessionId);
  const railItems: RailItem[] = GAMES.map((g) => ({
    id: g.id,
    icon: g.icon,
    tip: g.tip,
    active: g.id === genre,
    href: g.id === genre ? `/host/sessions/${sessionId}` : `/create?game=${g.id}`,
  }));

  const loadPoints = async () => setPoints(await client.rollbackPoints(sessionId));
  useEffect(() => {
    loadPoints().catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId]);
  const [playerLink, setPlayerLink] = useState("");
  useEffect(() => {
    if (!sessionId) return;
    client
      .getSession(sessionId)
      .then((summary) => {
        setSessionGameHint(`${summary.game_id ?? ""} ${String(summary.script_path ?? "")}`);
        // 取第一个玩家链接作为快捷入口
        const links = summary.player_links ?? {};
        const first = Object.values(links)[0];
        if (first) {
          try { setPlayerLink(new URL(first).pathname + new URL(first).search); }
          catch { setPlayerLink(first); }
        }
      })
      .catch(() => setSessionGameHint(""));
  }, [client, sessionId]);

  return (
    <ImmersiveShell genre={themeGenreForKind(genre)} railItems={railItems}>
      <div className="host-workspace">
        {/* 左栏：控制 */}
        <div className="host-left">
          <div>
            <div className="host-section-title">生命周期</div>
            <div className="ctrl-group">
              <div className="ctrl-row">
                <button className="btn" disabled={busy} onClick={() => run(() => client.assign(sessionId))}>发牌</button>
                <button className="btn primary" disabled={busy} onClick={() => run(async () => { await client.assign(sessionId).catch(() => {}); await client.start(sessionId).catch(() => {}); })}>开始</button>
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
        <main className="stage">
          <Topbar
            title="主持人控制台"
            sub={sessionId}
            phase={inbox.phase}
            right={
              <>
                <div className="view-links">
                  <Link className="view-link active" to={`/host/sessions/${sessionId}`}>主持</Link>
                  <Link className="view-link" to={`/viewer/sessions/${sessionId}`}>观众</Link>
                  <Link className="view-link" to={playerLink || `/player?token=${sessionId}:Player_1`}>玩家</Link>
                </div>
                <ConnStatus status={inbox.status} />
              </>
            }
          />
          {view?.players?.length ? <RoundTable players={view.players} lastSpeech={lastSpeech} /> : null}
          <Channels channels={channels} active={activeChannel} onSelect={setActiveChannel} />
          <MessageFeed messages={filter(inbox.messages, activeChannel)} />
        </main>

        {/* 右栏：状态面板（复用 Sidebar 的内容以只读呈现） */}
        <Sidebar view={view} />
      </div>
    </ImmersiveShell>
  );
}
