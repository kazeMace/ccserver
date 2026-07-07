// 玩家页：token 认领座位 → 消息流 + 待回复 Composer + 侧边栏。
// §2.3 铁律：inbox 已是该 seat 的 per-seat 投影，前端不做任何可见性过滤。

import { useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Topbar, ConnStatus } from "../components/Chrome";
import { MessageFeed } from "../components/MessageFeed";
import { Composer } from "../components/Composer";
import { Sidebar } from "../components/Sidebar";
import { useInbox } from "../hooks/useInbox";
import { useStateView } from "../hooks/useStateView";

export function PlayerPage() {
  const [params] = useSearchParams();
  const token = params.get("token") ?? "";
  // 约定：token 形如 "<sessionId>" 或 "<sessionId>:<seat>"；mock 下 token = sessionId。
  const { sessionId, seat } = useMemo(() => {
    const [sid, s] = token.split(":");
    return { sessionId: sid, seat: s || "Player_1" };
  }, [token]);
  const [sideOpen, setSideOpen] = useState(false);

  const inbox = useInbox(sessionId, `player:${seat}`);
  const view = useStateView(sessionId, `player:${seat}`);
  const [submitError, setSubmitError] = useState("");

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
        <MessageFeed messages={inbox.messages} selfSeat={seat} />
        {submitError ? <div className="c-cond" style={{ textAlign: "center", padding: "4px 0" }}>{submitError}</div> : null}
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
      </div>
      <Sidebar view={view} open={sideOpen} onClose={() => setSideOpen(false)} />
    </div>
  );
}
