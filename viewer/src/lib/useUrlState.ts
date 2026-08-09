import { useEffect, useState } from "react";

// Lightweight URL-query state: React state mirrored into window.location.search, so a
// refresh (or a shared link) restores the page. Params equal to their default are omitted
// to keep URLs clean. History is updated via replaceState (no back/forward spam per keystroke).

export function getParam(key: string, fallback = ""): string {
  return new URLSearchParams(window.location.search).get(key) ?? fallback;
}

function writeParam(key: string, value: string, initial: string) {
  const p = new URLSearchParams(window.location.search);
  if (value === initial) p.delete(key);
  else p.set(key, value);
  const qs = p.toString();
  window.history.replaceState(null, "", qs ? `?${qs}` : window.location.pathname);
}

/** Clear every query param (used when switching top-level view). */
export function clearParams() {
  window.history.replaceState(null, "", window.location.pathname);
}

export function useUrlState(key: string, initial: string): [string, (v: string) => void] {
  const [value, setValue] = useState(() => getParam(key, initial));
  const set = (v: string) => {
    setValue(v);
    writeParam(key, v, initial);
  };
  useEffect(() => {
    const onPop = () => setValue(getParam(key, initial));
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, [key, initial]);
  return [value, set];
}

/** Numeric variant for offsets/pages. */
export function useUrlNumber(key: string, initial: number): [number, (v: number) => void] {
  const [str, setStr] = useUrlState(key, String(initial));
  return [Number(str) || initial, (v: number) => setStr(String(v))];
}
