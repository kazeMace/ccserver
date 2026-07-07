// 观众页：只投影 public 消息流 + 只读侧边栏，无提交（seat=audience，pending 恒 null）。

import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { getClient } from "../api/client";
import { GAMES } from "../api/mockData";
import { inferGameKind, themeGenreForKind } from "../api/gamePresentation";
import { ImmersiveShell, type RailItem } from "../components/AppShell";
import { Topbar, ConnStatus } from "../components/Chrome";
import { MessageFeed } from "../components/MessageFeed";
import { Sidebar } from "../components/Sidebar";
import { useInbox } from "../hooks/useInbox";
import { useStateView } from "../hooks/useStateView";

export function ViewerPage() {
  const { sessionId = "" } = useParams();
  const [sideOpen, setSideOpen] = useState(false);
  const [sessionGameHint, setSessionGameHint] = useState("");
  const inbox = useInbox(sessionId, "audience");
  const view = useStateView(sessionId, "audience");
  useEffect(() => {
    if (!sessionId) return;
    getClient()
      .getSession(sessionId)
      .then((summary) => setSessionGameHint(`${summary.game_id ?? ""} ${String(summary.script_path ?? "")}`))
      .catch(() => setSessionGameHint(""));
  }, [sessionId]);
  const genre = inferGameKind(view?.panels?.genre as string) ?? inferGameKind(sessionGameHint) ?? inferGameKind(sessionId);
  const railItems: RailItem[] = GAMES.map((g) => ({
    id: g.id,
    icon: g.icon,
    tip: g.tip,
    active: g.id === genre,
    href: g.id === genre ? `/viewer/sessions/${sessionId}` : `/create?game=${g.id}`,
  }));

  return (
    <ImmersiveShell genre={themeGenreForKind(genre)} railItems={railItems}>
      <div className="stage">
        <Topbar
          title="观众视角"
          sub="仅公开信息"
          phase={inbox.phase}
          right={
            <>
              <div className="view-links">
                <Link className="view-link" to={`/host/sessions/${sessionId}`}>主持</Link>
                <Link className="view-link active" to={`/viewer/sessions/${sessionId}`}>观众</Link>
                <Link className="view-link" to={`/player?token=${sessionId}:Player_1`}>玩家</Link>
              </div>
              <ConnStatus status={inbox.status} />
              <button className="icon-btn mobile-only" onClick={() => setSideOpen(true)}>ℹ️</button>
            </>
          }
        />
        <MessageFeed messages={inbox.messages} />
      </div>
      <Sidebar view={view} open={sideOpen} onClose={() => setSideOpen(false)} />
    </ImmersiveShell>
  );
}
