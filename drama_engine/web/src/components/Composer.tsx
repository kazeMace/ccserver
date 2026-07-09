// 回复输入区（§3 ReplyRequest → 8 原语）的聊天式外壳。
// 输入原语与降级链统一实现在 inputs.tsx，这里只负责聊天流下方的容器与标题/提示行。
// 覆盖全部 8 原语：observe / text / choice / multi_choice / choice_or_text / vote / structured / form。

import type { ReplyRequest } from "../types/interaction";
import { PromptRow, ReplyInput, type SubmitPartial } from "./inputs";

export type { SubmitPartial } from "./inputs";

export interface ComposerProps {
  reply: ReplyRequest | null;
  status?: string;
  submitting?: boolean;
  onSubmit: (partial: SubmitPartial) => void;
}

export function Composer({ reply, status, submitting, onSubmit }: ComposerProps) {
  // 无待回复：显示等待/结束态。
  if (!reply) {
    if (status === "ended" || status === "failed") {
      return <div className="composer"><div className="composer-inner"><div className="waiting-tag">对局已结束</div></div></div>;
    }
    return (
      <div className="composer">
        <div className="composer-inner">
          <div className="waiting-tag"><span className="spin" />等待其他玩家 / 系统推进……</div>
        </div>
      </div>
    );
  }

  return (
    <div className="composer">
      <div className="composer-inner">
        <div className="composer-title">
          <span>行动输入</span>
          <span>{reply.primitive === "choice_or_text" ? "选项 / 自由对话" : reply.primitive}</span>
        </div>
        <PromptRow reply={reply} />
        <ReplyInput reply={reply} submitting={submitting} onSubmit={onSubmit} />
      </div>
    </div>
  );
}
