import type { Role } from "./types";

/**
 * Bearer-token session for the browser client.
 *
 * Scope, stated honestly: a development-grade session holder that exchanges
 * an identity for a token via the gated `/v1/auth/dev-token` route. A real
 * deployment replaces `login()` with an OIDC redirect against Amex's
 * card-member / merchant portal SSO and stores the token in an HttpOnly,
 * SameSite=Strict cookie. `authHeader()` and `getStreamToken()` are the
 * only two seams that change.
 *
 * sessionStorage rather than localStorage: the token dies with the tab
 * instead of persisting on a shared machine. It remains readable by any
 * script on this origin, which is exactly why an HttpOnly cookie is the
 * production answer and this is not.
 */

const STORAGE_KEY = "arbiter.session";

export interface Session {
  token: string;
  role: Role;
  boundId: string | null;
  actorId: string;
  expiresAt: number;
}

type Listener = (session: Session | null) => void;
const listeners = new Set<Listener>();

function storage(): Storage | null {
  try {
    return window.sessionStorage;
  } catch {
    return null; // private mode / storage blocked
  }
}

export function getSession(): Session | null {
  const raw = storage()?.getItem(STORAGE_KEY);
  if (!raw) return null;
  try {
    const session = JSON.parse(raw) as Session;
    // An expired token is treated as absent rather than sent and 401'd,
    // so the UI shows a sign-in prompt instead of an error.
    if (session.expiresAt <= Date.now()) {
      clearSession();
      return null;
    }
    return session;
  } catch {
    return null;
  }
}

export function setSession(session: Session): void {
  storage()?.setItem(STORAGE_KEY, JSON.stringify(session));
  listeners.forEach((l) => l(session));
}

export function clearSession(): void {
  storage()?.removeItem(STORAGE_KEY);
  listeners.forEach((l) => l(null));
}

export function subscribe(listener: Listener): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function authHeader(): Record<string, string> {
  const session = getSession();
  return session ? { Authorization: `Bearer ${session.token}` } : {};
}

export function isPrivileged(session: Session | null): boolean {
  return session?.role === "REVIEWER" || session?.role === "ADMIN";
}

/** Roles whose token is bound to one party, so the API scopes every read. */
export function isParty(session: Session | null): boolean {
  return session?.role === "CARD_MEMBER" || session?.role === "MERCHANT";
}
