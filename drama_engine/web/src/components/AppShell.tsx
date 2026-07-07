// 页面外壳：复用目标原型的 rail + stage 结构。
// Rail 只负责入口导航，不承载业务状态，避免页面和视觉组件互相耦合。

import type { ReactNode } from "react";
import { Link } from "react-router-dom";

export interface RailItem {
  id: string;
  icon: string;
  tip: string;
  active?: boolean;
  href?: string;
  onClick?: () => void;
}

export function AppRail({ items }: { items: RailItem[] }) {
  return (
    <nav className="rail">
      <Link className="rail-logo" to="/" aria-label="Drama Engine">
        D
      </Link>
      {items.map((item) => {
        const cls = `rail-item${item.active ? " active" : ""}`;
        if (item.href) {
          return (
            <Link key={item.id} className={cls} to={item.href} aria-label={item.tip}>
              {item.icon}
              <span className="rail-tip">{item.tip}</span>
            </Link>
          );
        }
        return (
          <button key={item.id} className={cls} onClick={item.onClick} aria-label={item.tip}>
            {item.icon}
            <span className="rail-tip">{item.tip}</span>
          </button>
        );
      })}
    </nav>
  );
}

export function MobileNav({ items }: { items: RailItem[] }) {
  return (
    <nav className="mobile-nav">
      {items.map((item) => {
        const cls = `mnav-item${item.active ? " active" : ""}`;
        if (item.href) {
          return (
            <Link key={item.id} className={cls} to={item.href}>
              <span className="mi">{item.icon}</span>
              {item.tip}
            </Link>
          );
        }
        return (
          <button key={item.id} className={cls} onClick={item.onClick}>
            <span className="mi">{item.icon}</span>
            {item.tip}
          </button>
        );
      })}
    </nav>
  );
}

export function ImmersiveShell({
  genre,
  railItems,
  children,
}: {
  genre?: string;
  railItems?: RailItem[];
  children: ReactNode;
}) {
  return (
    <div className="app" data-genre={genre}>
      {railItems?.length ? <AppRail items={railItems} /> : null}
      {children}
      {railItems?.length ? <MobileNav items={railItems} /> : null}
    </div>
  );
}
