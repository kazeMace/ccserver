// 玩家页：token 认领座位 → 频道切换 + 消息流 + 待回复 Composer + 侧边栏。
// 采用原型聊天范式：多频道（公开/狼人/私聊 = scope）+ 气泡消息流 + 自适应输入。
// §2.3 铁律：inbox 已是该 seat 的 per-seat 投影，前端不做任何可见性过滤，频道仅按 scope 分组展示。

import { useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Topbar, Channels, ConnStatus } from "../components/Chrome";
import { MessageFeed } from "../components/MessageFeed";
import { Composer } from "../components/Composer";
import { Sidebar } from "../components/Sidebar";
import { useInbox } from "../hooks/useInbox";
import { useStateView } from "../hooks/useStateView";
import { useChannels } from "../hooks/useChannels";

export function PlayerPage() {
  const [params] = useSearchParams();
  const token = params.get("token") ?? "";
  const { sessionId, seat } = useMemo(() => {
    const [sid, s] = token.split(":");
    return { sessionId: sid, seat: s || "Player_1" };
  }, [token]);

  const [sideOpen, setSideOpen] = useState(false);
  const [activeChannel, setActiveChannel] = useState("public");
  const [submitError, setSubmitError] = useState("");

  const inbox = useInbox(sessionId, `player:${seat}`);
  const view = useStateView(sessionId, `player:${seat}`);

  // game_pack 语义化频道名（scopeLabels）从 view.panels.scope_labels 取，未知走通用命名。
  const scopeLabels = (view?.panels?.scope_labels as Record<string, [string, string]>) ?? {};
  const { channels, filter } = useChannels(inbox.messages, scopeLabels);
  const shownMessages = filter(inbox.messages, activeChannel);
  const isPublic = activeChannel === "public";

  if (!token) {
    return (
      <div className="center-page">
        <div className="panel-card">
          <h1>玩家视角</h1>
          <div className="empty-hint">缺少 token。请通过主持人分发的玩家链接进入。</div>
        </div>
      </div>
    );
  }

  const genre = (view?.panels?.genre as string) ?? undefined;

  return (
    <div className="app" data-genre={genre}>
      <div className="stage">
        <Topbar
          title={`玩家 · ${seat}`}
          phase={inbox.phase}
          right={
            <>
              <ConnStatus status={inbox.status} />
              <button className="icon-btn mobile-only" onClick={() => setSideOpen(true)}>ℹ️</button>
            </>
          }
        />
        <Channels channels={channels} active={activeChannel} onSelect={setActiveChannel} />
        <MessageFeed messages={shownMessages} selfSeat={seat} />
        {submitError ? <div className="c-cond" style={{ textAlign: "center", padding: "4px 0" }}>{submitError}</div> : null}
        {/* 私密频道只读（原型范式）：待回复始终挂在玩家的主交互流；切到子频道仅查看。 */}
        {isPublic ? (
          <Composer
            reply={inbox.pending}
            status={inbox.status}
            submitting={inbox.submitting}
            onSubmit={async (p) => {
              setSubmitError("");
              try {
                await inbox.submit(p);
              } catch (e) {
                setSubmitError(String(e instanceof Error ? e.message : e));
              }
            }}
          />
        ) : (
          <div className="composer">
            <div className="composer-inner">
              <div className="waiting-tag">
                🔒 你正在查看「{channels.find((c) => c.id === activeChannel)?.name}」频道 · 切回全场继续行动
              </div>
            </div>
          </div>
        )}
      </div>
      <Sidebar view={view} open={sideOpen} onClose={() => setSideOpen(false)} />
    </div>
  );
}
