// 创建房间页：选游戏 + 座位数 + 真人数 + dry_run 开关 + 结果链接。
// 对齐旧 create.js 全部功能并扩展（旧版写死单剧本 + 隐藏 dry_run，这里都可编辑）。

import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { getClient, type GameDef, type GameRoleDef, type SessionSummary } from "../api/client";
import { GAMES } from "../api/mockData";
import { GAME_META, inferGameKind, themeGenreForKind } from "../api/gamePresentation";
import { ImmersiveShell, type RailItem } from "../components/AppShell";
import { Topbar } from "../components/Chrome";

export function CreatePage() {
  const [params] = useSearchParams();
  const [games, setGames] = useState<GameDef[]>([]);
  const [gameId, setGameId] = useState("");
  const [roles, setRoles] = useState<GameRoleDef[]>([]);
  const [dryRun, setDryRun] = useState(false); // 默认关闭 = 真实 LLM
  const [creating, setCreating] = useState(false);
  const [result, setResult] = useState<SessionSummary | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    getClient()
      .listGames()
      .then((gs) => {
        setGames(gs);
        const requestedKind = inferGameKind(params.get("game"));
        const requestedGame = requestedKind ? findGameForKind(gs, requestedKind) : null;
        if (requestedGame) setGameId(requestedGame.game_id);
        else if (requestedKind) setError(`当前目录没有 ${GAME_META.get(requestedKind)?.name ?? requestedKind} 对应脚本`);
        else if (gs.length) setGameId(gs[0].game_id);
      })
      .catch((e) => setError(String(e)));
  }, [params]);

  useEffect(() => {
    const game = games.find((g) => g.game_id === gameId);
    if (!game) {
      setRoles([]);
      return;
    }
    setRoles(importRolesFromGame(game));
  }, [games, gameId]);

  const create = async () => {
    setCreating(true);
    setError("");
    try {
      const game = games.find((g) => g.game_id === gameId);
      const seatIds = roles.map((r) => r.seat_id).filter(Boolean);
      const humanSeatIds = roles.filter((r) => r.controller === "human").map((r) => r.seat_id).filter(Boolean);
      if (!seatIds.length) throw new Error("至少需要 1 个角色座位");
      const summary = await getClient().createSession({
        game_id: gameId,
        script_path: game?.script_path,
        seat_ids: seatIds,
        human_seat_ids: humanSeatIds,
        params: { total_players: seatIds.length, dry_run: dryRun, use_runner: true, roles },
        metadata: { roles },
      });
      setResult(summary);
    } catch (e) {
      setError(String(e));
    } finally {
      setCreating(false);
    }
  };

  const activeKind = inferGameKind(gameId);
  const activeMeta = activeKind ? GAME_META.get(activeKind) : undefined;
  const humanCount = roles.filter((r) => r.controller === "human").length;
  const selectGame = (nextGameId: string) => {
    setGameId(nextGameId);
    setResult(null);
    setError("");
  };
  const selectKind = (kind: NonNullable<ReturnType<typeof inferGameKind>>) => {
    const target = findGameForKind(games, kind);
    if (!target) {
      setError(`当前目录没有 ${GAME_META.get(kind)?.name ?? kind} 对应脚本`);
      return;
    }
    selectGame(target.game_id);
  };
  const railItems: RailItem[] = GAMES.map((g) => {
    const kind = inferGameKind(g.id);
    const target = kind ? findGameForKind(games, kind) : null;
    return {
      id: g.id,
      icon: g.icon,
      tip: g.tip,
      active: target?.game_id === gameId,
      onClick: () => kind && selectKind(kind),
    };
  });

  return (
    <ImmersiveShell genre={themeGenreForKind(activeKind)} railItems={railItems}>
      <main className="stage launcher-stage">
        <Topbar title="Drama Engine" sub="创建一局游戏" phase={`${getClient().mode} 模式`} />
        <div className="launcher-body">
          <section className="launcher-panel">
            <h1>{activeMeta?.name ?? "选择游戏"}</h1>
            <div className="sub">{activeMeta?.sub ?? "interaction.v1 统一交互前端"}</div>

            {!result ? (
              <>
                <div className="form-group">
                  <label>选择游戏</label>
                  <select className="form-control" value={gameId} onChange={(e) => selectGame(e.target.value)}>
                    {games.map((g) => (
                      <option key={g.game_id} value={g.game_id}>
                        {g.title}
                      </option>
                    ))}
                  </select>
                </div>
                <div className="form-row">
                  <div className="form-group">
                    <label>角色总数</label>
                    <input className="form-control" value={roles.length} readOnly />
                  </div>
                  <div className="form-group">
                    <label>真人角色</label>
                    <input className="form-control" value={humanCount} readOnly />
                  </div>
                </div>
                <RoleImporter roles={roles} onChange={setRoles} />
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
          </section>

          <aside className="launcher-side">
            <div className="side-section">
              <div className="side-title">交互实现</div>
              <div className="stat-row">
                <span className="si">💬</span>
                <span className="sn">主循环</span>
                <span className="sv">消息流 + 回复</span>
              </div>
              <div className="stat-row">
                <span className="si">🔀</span>
                <span className="sn">差异化</span>
                <span className="sv">genre + widget</span>
              </div>
              <div className="stat-row">
                <span className="si">🧩</span>
                <span className="sn">协议</span>
                <span className="sv">interaction.v1</span>
              </div>
            </div>
            <div className="side-section">
              <div className="side-title">游戏范式</div>
              <div className="circles">
                {GAMES.map((g) => (
                  <span key={g.id} className={`circle-tag${g.id === gameId ? " active" : ""}`}>
                    {g.icon} {g.tip}
                  </span>
                ))}
              </div>
            </div>
          </aside>
        </div>
      </main>
    </ImmersiveShell>
  );
}

function importRolesFromGame(game: GameDef): GameRoleDef[] {
  const roles: GameRoleDef[] = game.roles?.length
    ? game.roles
    : (game.default_seat_ids ?? ["Player_1"]).map((seat, index) => ({
        seat_id: seat,
        name: seat,
        controller: index === 0 ? "human" : "ai",
      }));
  const humanSeats = new Set(game.default_human_seat_ids ?? roles.filter((r) => r.controller === "human").map((r) => r.seat_id));
  return roles.map((r, index) => ({
    seat_id: r.seat_id || `Player_${index + 1}`,
    name: r.name || r.seat_id || `角色 ${index + 1}`,
    emoji: r.emoji ?? "🙂",
    role: r.role ?? "",
    controller: humanSeats.has(r.seat_id) || r.controller === "human" ? "human" : r.controller ?? "ai",
    description: r.description ?? "",
  }));
}

function RoleImporter({ roles, onChange }: { roles: GameRoleDef[]; onChange: (roles: GameRoleDef[]) => void }) {
  const patchRole = (seatId: string, patch: Partial<GameRoleDef>) => {
    onChange(roles.map((r) => (r.seat_id === seatId ? { ...r, ...patch } : r)));
  };
  const addRole = () => {
    const next = roles.length + 1;
    onChange([...roles, { seat_id: `Player_${next}`, name: `角色 ${next}`, emoji: "🙂", controller: "ai", role: "" }]);
  };
  const removeRole = (seatId: string) => {
    if (roles.length <= 1) return;
    onChange(roles.filter((r) => r.seat_id !== seatId));
  };
  return (
    <div className="form-group">
      <div className="role-editor-head">
        <label>默认角色导入</label>
        <button className="btn sm" type="button" onClick={addRole}>添加角色</button>
      </div>
      <div className="role-editor">
        {roles.map((r) => (
          <div className="role-edit-row" key={r.seat_id}>
            <input className="role-emoji-input" value={r.emoji ?? ""} onChange={(e) => patchRole(r.seat_id, { emoji: e.target.value })} aria-label={`${r.seat_id} emoji`} />
            <div className="role-edit-main">
              <div className="role-edit-line">
                <input className="form-control compact" value={r.seat_id} onChange={(e) => patchRole(r.seat_id, { seat_id: e.target.value })} aria-label="座位 ID" />
                <input className="form-control compact" value={r.name} onChange={(e) => patchRole(r.seat_id, { name: e.target.value })} aria-label="角色名" />
              </div>
              <input className="form-control compact" value={r.role ?? ""} onChange={(e) => patchRole(r.seat_id, { role: e.target.value })} placeholder="身份 / 职能" />
              {r.description ? <div className="role-edit-desc">{r.description}</div> : null}
            </div>
            <button
              className={`controller-toggle${r.controller === "human" ? " human" : ""}`}
              type="button"
              onClick={() => patchRole(r.seat_id, { controller: r.controller === "human" ? "ai" : "human" })}
            >
              {r.controller === "human" ? "真人" : "AI"}
            </button>
            <button className="btn sm" type="button" disabled={roles.length <= 1} onClick={() => removeRole(r.seat_id)}>删除</button>
          </div>
        ))}
      </div>
    </div>
  );
}

function findGameForKind(games: GameDef[], kind: NonNullable<ReturnType<typeof inferGameKind>>): GameDef | null {
  if (kind === "the_clause") {
    return games.find((g) => isTheClauseGame(g)) ?? null;
  }
  const candidates = games.filter((g) => (inferGameKind(g.game_id) ?? inferGameKind(g.script_path) ?? inferGameKind(g.title)) === kind);
  if (!candidates.length) return null;
  return candidates[0];
}

function isTheClauseGame(game: GameDef): boolean {
  const gameId = game.game_id.toLowerCase();
  const scriptPath = game.script_path.toLowerCase();
  const title = game.title.toLowerCase();
  return (
    gameId === "the_clause" ||
    gameId === "the-clause" ||
    scriptPath.endsWith("/the_clause.yaml") ||
    scriptPath.endsWith("\\the_clause.yaml") ||
    title === "the clause"
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
        <Link className="btn primary" to={hostUrl}>进入主持台</Link>
        <Link className="btn" to={viewerUrl}>观众视角</Link>
      </div>
      {Object.keys(links).length ? (
        <div className="result-links">
          <label>玩家链接</label>
          {Object.entries(links).map(([seat, url]) => (
            <div className="link-row" key={seat}>
              <span className="seat">{seat}</span>
              <Link to={url}>{url}</Link>
              <button className="btn sm" onClick={() => navigator.clipboard?.writeText(location.origin + url)}>复制</button>
            </div>
          ))}
        </div>
      ) : null}
      <button className="btn" style={{ width: "100%", marginTop: 16 }} onClick={onReset}>再建一局</button>
    </div>
  );
}
