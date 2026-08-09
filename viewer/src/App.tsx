import { useState } from "react";

import AssetsView from "./AssetsView";
import NamesView from "./NamesView";
import { api } from "./lib/api";

type View = "names" | "assets";

export default function App() {
  const [view, setView] = useState<View>("names");

  return (
    <div className="app">
      <header>
        <h1>Stash Performer ID</h1>
        <nav className="views">
          <button className={view === "names" ? "active" : ""} onClick={() => setView("names")}>
            Names
          </button>
          <button className={view === "assets" ? "active" : ""} onClick={() => setView("assets")}>
            Assets
          </button>
        </nav>
        <span className="api">{api.base}</span>
      </header>

      {view === "names" ? <NamesView /> : <AssetsView />}
    </div>
  );
}
