// 创建房间页：选游戏 + 座位数 + 真人数 + dry_run 开关 + 结果链接。
// 对齐旧 create.js 全部功能并扩展（旧版写死单剧本 + 隐藏 dry_run，这里都可编辑）。

import { useEffect, useState } from "react";
import { getClient, type GameDef, type SessionSummary } from "../api/client";

export function CreatePage() {
  const [games, setGames] = useState<GameDef[]>([]);
  const [gameId, setGameId] = useState("");
  const [totalPlayers, setTotalPlayers] = useState(9);
  const [humanCount, setHumanCount] = useState(1);
  const [dryRun, setDryRun] = useState(false); // 默认关闭 = 真实 LLM
  const [creating, setCreating] = useState(false);
  const [result, setResult] = useState<SessionSummary | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    getClient()
      .listGames()
      .then((gs) => {
        setGames(gs);
        if (gs.length) setGameId(gs[0].game_id);
      })
      .catch((e) => setError(String(e)));
  }, []);

  const create = async () => {
    setCreating(true);
    setError("");
    try {
      const seatIds = Array.from({ length: totalPlayers }, (_, i) => `Player_${i + 1}`);
      const humanSeatIds = seatIds.slice(0, Math.min(humanCount, totalPlayers));
      const game = games.find((g) => g.game_id === gameId);
      const summary = await getClient().createSession({
        game_id: gameId,
        script_path: game?.script_path,
        seat_ids: seatIds,
        human_seat_ids: humanSeatIds,
        params: { total_players: totalPlayers, dry_run: dryRun, use_runner: true },
      });
      setResult(summary);
    } catch (e) {
      setError(String(e));
    } finally {
      setCreating(false);
    }
  };

  return (
    <div className="center-page">
      <div className="panel-card">
        <h1>Drama Engine</h1>
        <div className="sub">创建一局游戏 · interaction.v1 · {getClient().mode} 模式</div>

        {!result ? (
          <>
            <div className="form-group">
              <label>选择游戏</label>
              <select className="form-control" value={gameId} onChange={(e) => setGameId(e.target.value)}>
                {games.map((g) => (
                  <option key={g.game_id} value={g.game_id}>
                    {g.title}
                  </option>
                ))}
              </select>
            </div>
            <div className="form-row">
              <div className="form-group">
                <label>玩家总数</label>
                <input className="form-control" type="number" min={1} max={12} value={totalPlayers} onChange={(e) => setTotalPlayers(Number(e.target.value))} />
              </div>
              <div className="form-group">
                <label>真人座位数</label>
                <input className="form-control" type="number" min={0} max={totalPlayers} value={humanCount} onChange={(e) => setHumanCount(Number(e.target.value))} />
              </div>
            </div>
            <div className="form-group">
              <label className="switch-row">
                <input type="checkbox" checked={dryRun} onChange={(e) => setDryRun(e.target.checked)} />
                干跑模式（MockActor，不调用真实 LLM）
              </label>
            </div>
            {error ? <div className="c-cond" style={{ marginBottom: 12 }}>{error}</div> : null}
            <button className="btn primary" style={{ width: "100%" }} disabled={creating || !gameId} onClick={create}>
              {creating ? "创建中……" : humanCount > 0 ? "创建房间并生成玩家链接" : "创建观战房间"}
            </button>
          </>
        ) : (
          <CreateResult summary={result} onReset={() => setResult(null)} />
        )}
      </div>
    </div>
  );
}

function CreateResult({ summary, onReset }: { summary: SessionSummary; onReset: () => void }) {
  const links = summary.player_links ?? {};
  const hostUrl = summary.host_url ?? `/host/sessions/${summary.session_id}`;
  const viewerUrl = summary.viewer_url ?? `/viewer/sessions/${summary.session_id}`;
  return (
    <div>
      <div className="link-row">
        <span className="seat">Session</span>
        <span style={{ fontFamily: "monospace", fontSize: 12 }}>{summary.session_id}</span>
      </div>
      <div className="ctrl-row" style={{ marginTop: 12 }}>
        <a className="btn primary" href={hostUrl}>进入主持台</a>
        <a className="btn" href={viewerUrl}>观众视角</a>
      </div>
      {Object.keys(links).length ? (
        <div className="result-links">
          <label>玩家链接</label>
          {Object.entries(links).map(([seat, url]) => (
            <div className="link-row" key={seat}>
              <span className="seat">{seat}</span>
              <a href={url}>{url}</a>
              <button className="btn sm" onClick={() => navigator.clipboard?.writeText(location.origin + url)}>复制</button>
            </div>
          ))}
        </div>
      ) : null}
      <button className="btn" style={{ width: "100%", marginTop: 16 }} onClick={onReset}>再建一局</button>
    </div>
  );
}
