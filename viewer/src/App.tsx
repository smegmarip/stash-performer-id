import AssetsView from "./AssetsView";
import NamesView from "./NamesView";
import { api } from "./lib/api";
import { clearParams, useUrlState } from "./lib/useUrlState";
import { AppShell } from "./ui/AppShell";
import type { NavItem } from "./ui/AppShell";

const NAV: NavItem[] = [
  { key: "names", label: "Names", icon: "icon-[tabler--tag]" },
  { key: "assets", label: "Assets", icon: "icon-[tabler--photo]" },
];

export default function App() {
  const [view, setViewRaw] = useUrlState("view", "names");

  // Switching top-level view clears the previous view's query params, then records the view.
  const setView = (v: string) => {
    clearParams();
    setViewRaw(v);
  };

  return (
    <AppShell nav={NAV} current={view} onNav={setView} apiBase={api.base}>
      {view === "names" ? <NamesView /> : <AssetsView />}
    </AppShell>
  );
}
