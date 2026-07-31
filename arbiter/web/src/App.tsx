import { useCallback, useEffect, useState } from "react";
import { NavLink, Outlet, useLocation } from "react-router-dom";
import SignIn from "./components/SignIn";
import { Badge, Button } from "./components/ui";
import { api } from "./lib/api";
import { cx } from "./lib/format";
import { clearSession, getSession, isPrivileged, subscribe, type Session } from "./lib/session";
import type { ReadyResponse } from "./lib/types";
import { useApi } from "./lib/useApi";

/**
 * Application shell: identity, navigation, and the service-health banner.
 *
 * Navigation is role-aware because the API is: a party-scoped token cannot
 * read the fairness dashboard, the rulepack registry, or the audit log, so
 * offering those links to a card member would only produce 403s. The menu
 * shows what the caller can actually do.
 */

interface NavItem {
  to: string;
  label: string;
  privileged?: boolean;
  end?: boolean;
}

const NAV: NavItem[] = [
  { to: "/", label: "Overview", end: true },
  { to: "/cases", label: "Cases" },
  { to: "/file", label: "File a dispute" },
  { to: "/fairness", label: "Fairness", privileged: true },
  { to: "/rulepacks", label: "Rulepacks", privileged: true },
  { to: "/provenance", label: "Provenance" },
  { to: "/operations", label: "Operations", privileged: true },
];

export default function App() {
  const [session, setSession] = useState<Session | null>(getSession);
  const [dark, setDark] = useState(() => document.documentElement.classList.contains("dark"));
  const location = useLocation();

  useEffect(() => subscribe(setSession), []);

  const toggleTheme = useCallback(() => {
    setDark((previous) => {
      const next = !previous;
      document.documentElement.classList.toggle("dark", next);
      try { localStorage.setItem("arbiter.theme", next ? "dark" : "light"); } catch { /* storage blocked */ }
      return next;
    });
  }, []);

  if (!session) return <SignIn onSignedIn={setSession} />;

  const privileged = isPrivileged(session);
  const items = NAV.filter((item) => !item.privileged || privileged);

  return (
    <div className="flex min-h-full flex-col bg-background selection:bg-primary/20 selection:text-primary">
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-4 focus:z-50 focus:rounded-xl focus:bg-primary focus:px-4 focus:py-2 focus:text-sm focus:font-bold focus:text-primary-foreground focus:shadow-lg"
      >
        Skip to content
      </a>

      {/* Premium Glass Header */}
      <header className="sticky top-0 z-30 border-b border-border/60 bg-background/80 backdrop-blur-xl">
        <div className="mx-auto flex max-w-[90rem] flex-wrap items-center gap-x-8 gap-y-4 px-4 py-3 sm:px-6 animate-in fade-in slide-in-from-top-4 duration-700">
          
          {/* Brand Block with subtle ambient bloom */}
          <div className="flex items-center gap-3 group relative">
            <div className="absolute -inset-2 rounded-lg bg-primary/20 blur-xl opacity-0 group-hover:opacity-100 transition-opacity duration-700 pointer-events-none dark:bg-primary/10" />
            <span
              aria-hidden="true"
              className="relative grid h-9 w-9 place-items-center rounded-[10px] bg-primary text-sm font-extrabold text-primary-foreground shadow-[0_2px_10px_oklch(var(--primary)/0.3)] transition-transform duration-300 group-hover:scale-105"
            >
              A
            </span>
            <div className="relative leading-none space-y-1">
              <p className="text-sm font-extrabold tracking-tight text-foreground">ARBITER</p>
              <p className="text-[9px] font-bold uppercase tracking-[0.2em] text-muted-foreground">Rules decide; models never do</p>
            </div>
          </div>

          {/* Elevated Navigation */}
          <nav aria-label="Primary" className="order-3 -mx-1 w-full overflow-x-auto scroll-x sm:order-none sm:mx-0 sm:w-auto sm:flex-1">
            <ul className="flex items-center gap-1.5">
              {items.map((item) => (
                <li key={item.to}>
                  <NavLink
                    to={item.to}
                    end={item.end}
                    className={({ isActive }) =>
                      cx(
                        "block whitespace-nowrap rounded-lg px-3 py-1.5 text-xs font-bold tracking-wide transition-all duration-300 border",
                        isActive
                          ? "bg-primary/10 text-primary border-primary/20 shadow-sm dark:bg-primary/15 dark:border-primary/30"
                          : "text-muted-foreground border-transparent hover:bg-secondary/60 hover:text-foreground"
                      )
                    }
                  >
                    {item.label}
                  </NavLink>
                </li>
              ))}
            </ul>
          </nav>

          {/* Actions & Identity */}
          <div className="ml-auto flex items-center gap-3">
            <ServiceStatus />
            
            <div className="h-6 w-px bg-border/60 mx-1 hidden sm:block" aria-hidden="true" />
            
            <button
              type="button"
              onClick={toggleTheme}
              aria-label={dark ? "Switch to light theme" : "Switch to dark theme"}
              className="rounded-lg p-2 text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground focus-visible:outline focus-visible:outline-2 focus-visible:outline-ring active:scale-95"
            >
              {dark ? (
                <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2"><path strokeLinecap="round" strokeLinejoin="round" d="M12 3v1m0 16v1m9-9h-1M4 12H3m15.364 6.364l-.707-.707M6.343 6.343l-.707-.707m12.728 0l-.707.707M6.343 17.657l-.707.707M16 12a4 4 0 11-8 0 4 4 0 018 0z" /></svg>
              ) : (
                <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2"><path strokeLinecap="round" strokeLinejoin="round" d="M20.354 15.354A9 9 0 018.646 3.646 9.003 9.003 0 0012 21a9.003 9.003 0 008.354-5.646z" /></svg>
              )}
            </button>
            
            <div className="hidden text-right sm:block leading-tight mr-2">
              <p className="text-[10px] font-bold uppercase tracking-widest text-primary">{session.role}</p>
              {session.boundId && (
                <p className="font-mono text-[11px] text-muted-foreground">{session.boundId.slice(0, 8)}</p>
              )}
            </div>
            
            <Button size="sm" variant="secondary" onClick={clearSession}>Sign out</Button>
          </div>
        </div>
      </header>

      {/* Main Content Area */}
      <main id="main" className="mx-auto w-full max-w-[90rem] flex-1 px-4 py-8 sm:px-6" key={location.pathname}>
        <Outlet />
      </main>

      {/* Footer */}
      <footer className="border-t border-border/50 py-6 bg-background/50">
        <p className="mx-auto max-w-[90rem] px-4 text-xs font-medium leading-relaxed text-muted-foreground sm:px-6 text-balance text-center sm:text-left">
          Every figure on this console is served by the ARBITER API. Nothing here is
          computed, cached, or estimated in the browser.
        </p>
      </footer>
    </div>
  );
}

/**
 * Service readiness. Shown for everyone, because a degraded conformal gate
 * changes what the system does to every case: an under-calibrated reason
 * code escalates instead of auto-resolving, and a user comparing two cases
 * deserves to know that is why.
 */
function ServiceStatus() {
  const state = useApi<ReadyResponse>(() => api.ready(), []);
  if (!state.data) return null;

  const ready = state.data.status === "ready";
  const degraded = Object.entries(state.data.calibration)
    .filter(([, v]) => !v.calibrated)
    .map(([code]) => code);

  return (
    <Badge
      className={cx(
        "hidden md:inline-flex items-center gap-1.5 px-2.5 py-1",
        ready
          ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 dark:border-emerald-500/20"
          : "border-amber-500/30 bg-amber-500/10 text-amber-600 dark:text-amber-400 dark:border-amber-500/20",
      )}
      title={
        ready
          ? "All reason codes have enough real calibration data to auto-resolve."
          : `Under-calibrated: ${degraded.join(", ")}. Those cases escalate to human review instead of auto-resolving.`
      }
    >
      {/* Live Ops Pulsing Dot */}
      <span className="relative flex h-2 w-2">
        {ready && <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75"></span>}
        <span className={cx("relative inline-flex rounded-full h-2 w-2", ready ? "bg-emerald-500 dark:bg-emerald-400" : "bg-amber-500 dark:bg-amber-400")}></span>
      </span>
      {ready ? "Ready" : `Degraded (${degraded.length})`}
    </Badge>
  );
}