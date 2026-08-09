import { useState } from "react";
import type { ReactNode } from "react";

import { ThemeToggle } from "./ThemeToggle";

// `icon` must be a full literal class (e.g. "icon-[tabler--tag]") so Tailwind detects it.
export type NavItem = { key: string; label: string; icon: string };

export function AppShell({
  nav,
  current,
  onNav,
  apiBase,
  children,
}: {
  nav: NavItem[];
  current: string;
  onNav: (key: string) => void;
  apiBase: string;
  children: ReactNode;
}) {
  const [drawerOpen, setDrawerOpen] = useState(false);

  const go = (key: string) => {
    onNav(key);
    setDrawerOpen(false);
  };

  return (
    <div className="bg-base-200 text-base-content flex min-h-screen flex-col">
      {/* Header */}
      <header className="bg-base-100 border-base-content/20 lg:ps-64 sticky top-0 z-40 border-b">
        <div className="mx-auto w-full max-w-7xl">
          <nav className="navbar gap-2 px-4 py-2">
            <div className="navbar-start items-center gap-2">
              <button
                type="button"
                className="btn btn-soft btn-square btn-sm lg:hidden"
                aria-label="Open menu"
                onClick={() => setDrawerOpen(true)}
              >
                <span className="icon-[tabler--menu-2] size-4.5" />
              </button>
              <span className="text-base font-semibold">Stash Performer ID</span>
            </div>
            <div className="navbar-end items-center gap-3">
              <span className="text-base-content/50 hidden text-xs sm:inline">{apiBase}</span>
              <ThemeToggle />
            </div>
          </nav>
        </div>
      </header>

      {/* Mobile backdrop */}
      {drawerOpen && (
        <div className="fixed inset-0 z-40 bg-black/40 lg:hidden" onClick={() => setDrawerOpen(false)} />
      )}

      {/* Sidebar */}
      <aside
        className={`bg-base-100 border-base-content/20 fixed inset-y-0 start-0 z-50 w-64 border-e transition-transform lg:translate-x-0 ${
          drawerOpen ? "translate-x-0" : "-translate-x-full"
        }`}
        aria-label="Sidebar"
      >
        <div className="flex h-full flex-col">
          <div className="border-base-content/20 flex items-center gap-2 border-b px-4 py-3">
            <span className="icon-[tabler--user-star] text-primary size-6" />
            <span className="font-semibold">Performer ID</span>
            <button
              type="button"
              className="btn btn-text btn-circle btn-sm ms-auto lg:hidden"
              aria-label="Close menu"
              onClick={() => setDrawerOpen(false)}
            >
              <span className="icon-[tabler--x] size-4.5" />
            </button>
          </div>
          <ul className="menu menu-sm grow gap-1 p-3">
            {nav.map((n) => (
              <li key={n.key}>
                <button
                  type="button"
                  className={`w-full px-2 ${current === n.key ? "menu-active" : ""}`}
                  onClick={() => go(n.key)}
                >
                  <span className={`${n.icon} size-4.5`} />
                  <span className="grow text-start">{n.label}</span>
                </button>
              </li>
            ))}
          </ul>
        </div>
      </aside>

      {/* Content */}
      <div className="lg:ps-64 flex grow flex-col">
        <main className="mx-auto w-full max-w-[1280px] flex-1 grow space-y-6 p-4 sm:p-6">
          {children}
        </main>
      </div>
    </div>
  );
}
