import { useState } from "react";
import { ApiError, login } from "@/lib/api";
import type { Session } from "@/lib/session";
import type { Role } from "@/lib/types";
import { Button, Card, CardBody, Field, Select, TextInput } from "./ui";

/**
 * Sign-in.
 *
 * Deliberately explicit about which party you are acting as, because the
 * API enforces party binding: a MERCHANT token can only ever read that
 * merchant's cases, and a CARD_MEMBER token only that card member's.
 * Signing in as the wrong party produces a 403 — that is the authorization
 * layer working, not a bug, and the copy here says so.
 *
 * Development sign-in against the gated `/v1/auth/dev-token` route. A real
 * deployment replaces this component with an OIDC redirect; nothing
 * downstream changes.
 */

const ROLES: { role: Role; label: string; bound: boolean; hint: string; scope: string }[] = [
  {
    role: "CARD_MEMBER", label: "Card member", bound: true,
    hint: "card_member_id",
    scope: "Sees only their own disputes, with the merchant's evidence but not the rulepack.",
  },
  {
    role: "MERCHANT", label: "Merchant", bound: true,
    hint: "merchant_id",
    scope: "Sees only disputes filed against them. The card member's claim and identity nodes are filtered out.",
  },
  {
    role: "REVIEWER", label: "Analyst / reviewer", bound: false,
    hint: "",
    scope: "Sees every case, the audit log, the fairness dashboard, and the rulepack registry.",
  },
  {
    role: "ADMIN", label: "Administrator", bound: false,
    hint: "",
    scope: "Reviewer access plus GDPR erasure and the regulatory-clock sweeper.",
  },
];

export default function SignIn({ onSignedIn }: { onSignedIn: (session: Session) => void }) {
  const [role, setRole] = useState<Role>("REVIEWER");
  const [boundId, setBoundId] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  const selected = ROLES.find((r) => r.role === role)!;

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setError(null);

    if (selected.bound && !boundId.trim()) {
      setError(`A ${selected.label.toLowerCase()} token must be bound to a ${selected.hint}.`);
      return;
    }

    setPending(true);
    try {
      onSignedIn(await login(role, selected.bound ? boundId.trim() : null));
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : String(caught));
    } finally {
      setPending(false);
    }
  }

  return (
    <div className="relative flex min-h-screen items-center justify-center overflow-hidden bg-background px-4 py-10 selection:bg-primary/20 selection:text-primary">
      {/* Ambient Background Glows */}
      <div className="pointer-events-none absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 w-[800px] h-[800px] rounded-full bg-primary/5 blur-[120px] dark:bg-primary/10" />
      
      <div className="relative z-10 w-full max-w-md animate-in fade-in slide-in-from-bottom-8 duration-1000">
        
        {/* Brand Header */}
        <div className="mb-8 text-center">
          <div className="relative mx-auto grid h-14 w-14 place-items-center rounded-2xl bg-primary text-xl font-extrabold text-primary-foreground shadow-[0_8px_30px_oklch(var(--primary)/0.3)] transition-transform duration-500 hover:scale-105">
            <div className="absolute -inset-3 rounded-2xl bg-primary/20 blur-xl -z-10 animate-pulse" />
            A
          </div>
          <h1 className="mt-6 text-3xl font-extrabold tracking-tight text-foreground">
            ARBITER
          </h1>
          <p className="mt-2 text-sm font-medium text-muted-foreground">
            Auditable dispute adjudication for American Express
          </p>
        </div>

        {/* Login Form Card */}
        <Card className="shadow-2xl shadow-primary/5">
          <CardBody className="p-8">
            <form onSubmit={submit} className="space-y-6">
              <Field
                label="Act as"
                htmlFor="role"
                hint={selected.scope}
              >
                <Select
                  id="role"
                  value={role}
                  onChange={(next) => { setRole(next); setError(null); }}
                  options={ROLES.map((r) => ({ value: r.role, label: r.label }))}
                />
              </Field>

              {selected.bound && (
                <div className="animate-in fade-in slide-in-from-top-2 duration-300">
                  <Field
                    label={selected.hint.replace('_', ' ')}
                    htmlFor="bound-id"
                    required
                    hint="The UUID this token is bound to. Every case route is scoped to it server-side; there is no parameter that widens it."
                  >
                    <TextInput
                      id="bound-id"
                      mono
                      value={boundId}
                      onChange={setBoundId}
                      placeholder="00000000-0000-0000-0000-000000000000"
                    />
                  </Field>
                </div>
              )}

              {error && (
                <div 
                  role="alert" 
                  className="animate-in fade-in slide-in-from-top-2 rounded-xl border border-destructive/30 bg-destructive/10 p-4 text-xs font-medium leading-relaxed text-destructive dark:text-red-400"
                >
                  <div className="flex items-start gap-2">
                    <span className="text-base leading-none">⚠</span>
                    <span>{error}</span>
                  </div>
                </div>
              )}

              <div className="pt-2">
                <Button type="submit" variant="primary" pending={pending} className="w-full py-3 text-base shadow-lg shadow-primary/20">
                  Sign in to Console
                </Button>
              </div>
            </form>
          </CardBody>
        </Card>

        {/* Technical Footer / Disclaimer */}
        <p className="mt-8 mx-auto max-w-sm text-center text-[11px] leading-relaxed text-muted-foreground/80">
          Development sign-in, gated behind <code className="rounded bg-secondary/50 px-1 py-0.5 font-mono text-[10px] text-foreground">ARBITER_ENABLE_DEV_AUTH</code>,
          which is off by default and refuses to start outside <code className="rounded bg-secondary/50 px-1 py-0.5 font-mono text-[10px] text-foreground">env=dev</code>.
          Production issues tokens from Amex SSO.
        </p>
      </div>
    </div>
  );
}