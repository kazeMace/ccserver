// 角色卡片组件：展示角色详细信息（头像/人设/TTS 等）

import type { RoleDefinition } from "../types/interaction";

export interface RoleCardProps {
  roleId: string;
  role: RoleDefinition;
  variant?: "compact" | "full"; // compact: 列表项，full: 完整卡片
}

export function RoleCard({ roleId, role, variant = "compact" }: RoleCardProps) {
  if (variant === "compact") {
    return (
      <div className="role-card-compact">
        <div className="role-avatar">
          {role.portrait_url ? (
            <img src={role.portrait_url} alt={role.name} />
          ) : (
            <span className="role-emoji">{role.emoji ?? "👤"}</span>
          )}
        </div>
        <div className="role-info">
          <div className="role-name">{role.name}</div>
          {role.faction ? <div className="role-faction">{role.faction}</div> : null}
          {role.description && variant === "full" ? (
            <div className="role-description">{role.description}</div>
          ) : null}
        </div>
      </div>
    );
  }

  // full variant：完整卡片
  return (
    <div className="role-card-full">
      <div className="role-header">
        {role.portrait_url ? (
          <img src={role.portrait_url} alt={role.name} className="role-portrait" />
        ) : (
          <div className="role-portrait-placeholder">
            <span className="role-emoji-large">{role.emoji ?? "👤"}</span>
          </div>
        )}
        <div className="role-title">
          <h3>{role.name}</h3>
          {role.faction ? <span className="role-faction-tag">{role.faction}</span> : null}
        </div>
      </div>
      {role.description ? (
        <div className="role-body">
          <div className="role-description-full">{role.description}</div>
        </div>
      ) : null}
      {role.voice_id ? (
        <div className="role-footer">
          <span className="role-voice">🔊 {role.voice_id}</span>
        </div>
      ) : null}
    </div>
  );
}

// 角色列表组件：展示所有角色
export function RolesList({ roles }: { roles: Record<string, RoleDefinition> }) {
  const entries = Object.entries(roles);
  if (entries.length === 0) return null;

  return (
    <div className="roles-list">
      {entries.map(([roleId, role]) => (
        <RoleCard key={roleId} roleId={roleId} role={role} variant="compact" />
      ))}
    </div>
  );
}
