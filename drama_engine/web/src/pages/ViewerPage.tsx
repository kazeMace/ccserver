// 观众页：只投影 public 消息流 + 只读侧边栏，无提交（seat=audience，pending 恒 null）。

import { useState } from "react";
import { useParams } from "react-router-dom";
import { Topbar, ConnStatus } from "../components/Chrome";
import { MessageFeed } from "../components/MessageFeed";
import { Sidebar } from "../components/Sidebar";
import { useInbox } from "../hooks/useInbox";
import { useStateView } from "../hooks/useStateView";

export function ViewerPage() {
  const { sessionId = "" } = useParams();
  const [sideOpen, setSideOpen] = useState(false);
  const inbox = useInbox(sessionId, "audience");
  const view = useStateView(sessionId, "audience");

  return (
    <div className="app">
      <div className="stage">
        <Topbar
          title="观众视角"
          sub="仅公开信息"
          phase={inbox.phase}
          right={
            <>
              <ConnStatus status={inbox.status} />
              <button className="icon-btn mobile-only" onClick={() => setSideOpen(true)}>ℹ️</button>
            </>
          }
        />
        <MessageFeed messages={inbox.messages} />
      </div>
      <Sidebar view={view} open={sideOpen} onClose={() => setSideOpen(false)} />
    </div>
  );
}
