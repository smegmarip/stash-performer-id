import { useEffect, useState } from "react";

export function ThemeToggle() {
  const [dark, setDark] = useState(() => (localStorage.getItem("spid-theme") ?? "dark") === "dark");

  useEffect(() => {
    const t = dark ? "dark" : "light";
    document.documentElement.setAttribute("data-theme", t);
    localStorage.setItem("spid-theme", t);
  }, [dark]);

  return (
    <button
      type="button"
      className="btn btn-text btn-circle btn-sm"
      title="Toggle theme"
      onClick={() => setDark((d) => !d)}
    >
      <span className={`size-5 ${dark ? "icon-[tabler--sun]" : "icon-[tabler--moon]"}`} />
    </button>
  );
}
