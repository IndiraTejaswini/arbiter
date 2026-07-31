// import { useEffect, useRef, useState, type ReactNode } from "react";
// import { ApiError } from "@/lib/api";
// import { cx } from "@/lib/format";

// // -- Layout ---------------------------------------------------------------

// export function Card({
//   children, className, as: Tag = "section",
// }: { children: ReactNode; className?: string; as?: "section" | "div" | "article" }) {
//   return (
//     <Tag className={cx(
//       "relative overflow-hidden rounded-[16px] border border-border bg-card text-card-foreground shadow-sm group",
//       "transition-all duration-500 ease-out hover:-translate-y-1 hover:border-primary/40",
//       "hover:shadow-[0_8px_30px_rgba(0,0,0,0.06)] dark:hover:shadow-[0_8px_30px_rgba(0,0,0,0.4)]",
//       className,
//     )}>
//       {/* Premium Top Edge Glow Reveal */}
//       <div className="absolute top-0 left-0 h-[1px] w-full bg-gradient-to-r from-transparent via-primary to-transparent opacity-0 transition-opacity duration-500 group-hover:opacity-100" />
      
//       {/* Ambient Internal Corner Glow */}
//       <div className="pointer-events-none absolute -left-20 -top-20 h-48 w-48 rounded-full bg-primary/10 blur-[80px] transition-opacity duration-700 opacity-0 group-hover:opacity-100 dark:bg-primary/15" />
      
//       <div className="relative z-10">{children}</div>
//     </Tag>
//   );
// }

// export function CardHeader({
//   title, subtitle, actions, id,
// }: { title: ReactNode; subtitle?: ReactNode; actions?: ReactNode; id?: string }) {
//   return (
//     <div className="flex flex-wrap items-start justify-between gap-3 border-b border-border/50 px-6 py-4">
//       <div className="min-w-0">
//         <h2 id={id} className="text-sm font-bold tracking-tight text-foreground">{title}</h2>
//         {subtitle && (
//           <p className="mt-1 text-xs leading-relaxed text-muted-foreground">{subtitle}</p>
//         )}
//       </div>
//       {actions && <div className="flex shrink-0 items-center gap-3">{actions}</div>}
//     </div>
//   );
// }

// export function CardBody({ children, className }: { children: ReactNode; className?: string }) {
//   return <div className={cx("p-6", className)}>{children}</div>;
// }

// export function PageHeader({
//   eyebrow, title, description, actions,
// }: { eyebrow?: string; title: string; description?: ReactNode; actions?: ReactNode }) {
//   return (
//     <header className="mb-8 flex flex-wrap items-start justify-between gap-6 animate-in">
//       <div className="min-w-0 flex-1">
//         {eyebrow && (
//           <p className="mb-2 text-[10px] font-bold uppercase tracking-[0.2em] text-primary drop-shadow-[0_0_8px_oklch(var(--primary)/0.3)]">
//             {eyebrow}
//           </p>
//         )}
//         <h1 className="text-3xl font-extrabold tracking-tight text-foreground sm:text-4xl">
//           {title}
//         </h1>
//         {description && (
//           <div className="mt-3 max-w-2xl text-sm leading-relaxed text-muted-foreground text-balance">
//             {description}
//           </div>
//         )}
//       </div>
//       {actions && <div className="flex shrink-0 flex-wrap items-center gap-3">{actions}</div>}
//     </header>
//   );
// }

// // -- Controls -------------------------------------------------------------

// type ButtonVariant = "primary" | "secondary" | "ghost" | "danger";

// const BUTTON_VARIANTS: Record<ButtonVariant, string> = {
//   primary: "bg-primary text-primary-foreground shadow-[0_4px_14px_0_oklch(var(--primary)/0.25)] hover:brightness-110 border border-transparent",
//   secondary: "bg-secondary text-secondary-foreground hover:bg-secondary/80 border border-border/60 shadow-sm",
//   ghost: "text-foreground hover:bg-accent hover:text-accent-foreground border border-transparent",
//   danger: "bg-destructive text-destructive-foreground hover:brightness-110 shadow-sm border border-transparent",
// };

// export function Button({
//   children, onClick, variant = "secondary", disabled, pending, type = "button",
//   className, title, size = "md",
// }: {
//   children: ReactNode; onClick?: () => void; variant?: ButtonVariant;
//   disabled?: boolean; pending?: boolean; type?: "button" | "submit";
//   className?: string; title?: string; size?: "sm" | "md";
// }) {
//   return (
//     <button
//       type={type} onClick={onClick} title={title}
//       disabled={disabled || pending} aria-busy={pending || undefined}
//       className={cx(
//         "inline-flex items-center justify-center gap-2 rounded-xl font-medium transition-all duration-300",
//         "active:scale-[0.98] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring",
//         "disabled:cursor-not-allowed disabled:opacity-50 disabled:active:scale-100 disabled:hover:brightness-100",
//         size === "sm" ? "px-3 py-1.5 text-xs" : "px-4 py-2.5 text-sm",
//         BUTTON_VARIANTS[variant], className,
//       )}
//     >
//       {pending && <Spinner className="h-3.5 w-3.5" />}
//       {children}
//     </button>
//   );
// }

// export function Field({
//   label, hint, children, htmlFor, required,
// }: { label: string; hint?: ReactNode; children: ReactNode; htmlFor?: string; required?: boolean }) {
//   return (
//     <div className="space-y-1.5">
//       <label htmlFor={htmlFor} className="block text-[11px] font-bold uppercase tracking-wider text-foreground/80">
//         {label} {required && <span className="ml-1 text-destructive drop-shadow-[0_0_4px_oklch(var(--destructive)/0.5)]" aria-hidden="true">*</span>}
//       </label>
//       {children}
//       {hint && <p className="text-[11px] leading-relaxed text-muted-foreground">{hint}</p>}
//     </div>
//   );
// }

// const CONTROL_CLASS =
//   "w-full rounded-xl border border-input bg-background px-3.5 py-2.5 text-sm text-foreground transition-all duration-300 " +
//   "placeholder:text-muted-foreground focus:border-ring focus:bg-background focus:outline-none focus:ring-4 focus:ring-ring/10 " +
//   "hover:border-border/80 disabled:cursor-not-allowed disabled:opacity-50";

// export function TextInput(props: {
//   id?: string; value: string; onChange: (v: string) => void; placeholder?: string;
//   mono?: boolean; disabled?: boolean; type?: string; ariaLabel?: string;
// }) {
//   return (
//     <input
//       id={props.id} type={props.type ?? "text"} value={props.value}
//       aria-label={props.ariaLabel} disabled={props.disabled} placeholder={props.placeholder}
//       onChange={(e) => props.onChange(e.target.value)}
//       className={cx(CONTROL_CLASS, props.mono && "font-mono text-xs tracking-wider")}
//     />
//   );
// }

// export function TextArea(props: {
//   id?: string; value: string; onChange: (v: string) => void; placeholder?: string; rows?: number;
// }) {
//   return (
//     <textarea
//       id={props.id} rows={props.rows ?? 3} value={props.value}
//       placeholder={props.placeholder} onChange={(e) => props.onChange(e.target.value)}
//       className={cx(CONTROL_CLASS, "resize-y leading-relaxed")}
//     />
//   );
// }

// export function Select<T extends string>(props: {
//   id?: string; value: T; onChange: (v: T) => void;
//   options: { value: T; label: string }[]; ariaLabel?: string;
// }) {
//   return (
//     <select
//       id={props.id} value={props.value} aria-label={props.ariaLabel}
//       onChange={(e) => props.onChange(e.target.value as T)}
//       className={CONTROL_CLASS}
//     >
//       {props.options.map((o) => (
//         <option key={o.value} value={o.value}>{o.label}</option>
//       ))}
//     </select>
//   );
// }

// // -- Status & Polish ------------------------------------------------------

// export function Badge({
//   children, className, title,
// }: { children: ReactNode; className?: string; title?: string }) {
//   return (
//     <span
//       title={title}
//       className={cx(
//         "inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-[10px] font-bold uppercase tracking-[0.1em]",
//         "border border-primary/20 bg-primary/10 text-primary dark:border-primary/30 dark:bg-primary/15",
//         className,
//       )}
//     >
//       {children}
//     </span>
//   );
// }

// export function Spinner({ className }: { className?: string }) {
//   return (
//     <svg className={cx("animate-spin", className ?? "h-4 w-4")} viewBox="0 0 24 24" fill="none" aria-hidden="true">
//       <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
//       <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
//     </svg>
//   );
// }

// export function Skeleton({ className }: { className?: string }) {
//   return (
//     <div className={cx(
//       "relative overflow-hidden rounded-md bg-secondary/50",
//       className ?? "h-4 w-full",
//     )}>
//       <div className="absolute inset-0 -translate-x-full animate-shimmer" />
//     </div>
//   );
// }

// export function Async<T>({
//   state, children, empty, isEmpty, skeleton, label,
// }: {
//   state: { data: T | null; error: Error | null; loading: boolean; reload: () => void };
//   children: (data: T) => ReactNode; empty?: ReactNode; isEmpty?: (data: T) => boolean;
//   skeleton?: ReactNode; label?: string;
// }) {
//   if (state.loading && state.data === null) {
//     return (
//       <div role="status" aria-live="polite" aria-label={label ? `Loading ${label}` : "Loading"}>
//         {skeleton ?? (
//           <div className="space-y-4 p-4 animate-in">
//             <Skeleton className="h-4 w-1/3 rounded-full" />
//             <Skeleton className="h-4 w-2/3 rounded-full" />
//             <Skeleton className="h-4 w-1/2 rounded-full" />
//           </div>
//         )}
//       </div>
//     );
//   }
//   if (state.error) return <ErrorState error={state.error} onRetry={state.reload} />;
//   if (state.data === null) return null;
//   if (isEmpty?.(state.data)) {
//     return <>{empty ?? <EmptyState title="No records found" />}</>;
//   }
//   return <div className="animate-in">{children(state.data)}</div>;
// }

// export function ErrorState({ error, onRetry }: { error: Error; onRetry?: () => void }) {
//   const api = error instanceof ApiError ? error : null;
//   const isAuth = api?.isAuthError ?? false;
//   const offline = api?.status === 0;

//   return (
//     <div role="alert" className="flex flex-col items-center justify-center py-16 text-center animate-in px-4">
//       <div className={cx(
//         "text-5xl mb-4 opacity-80 filter drop-shadow-[0_0_15px_currentColor]",
//         isAuth ? "text-amber-500" : "text-destructive"
//       )}>⚠</div>
//       <div className="font-bold tracking-tight text-lg text-foreground">
//         {offline ? "Network Interruption" : isAuth ? "Access Restricted" : api?.isNotFound ? "Record Not Found" : "System Exception"}
//       </div>
//       <p className="mt-2 max-w-md text-sm leading-relaxed text-muted-foreground">{error.message}</p>
      
//       {isAuth && (
//         <p className="mt-3 max-w-md text-xs leading-relaxed text-muted-foreground/80">
//           Case routes are party-scoped. Ensure you are signed in as the filing member, the merchant, or an authorized reviewer.
//         </p>
//       )}
      
//       {onRetry && !isAuth && (
//         <div className="mt-6">
//           <Button onClick={onRetry} variant="secondary">Restore Connection</Button>
//         </div>
//       )}
//     </div>
//   );
// }

// export function EmptyState({
//   title, description, action,
// }: { title: string; description?: ReactNode; action?: ReactNode }) {
//   return (
//     <div className="flex flex-col items-center justify-center rounded-2xl border border-dashed border-border/60 bg-card/30 px-6 py-16 text-center transition-colors duration-500 hover:bg-card/60 animate-in">
//       <div className="mb-5 flex h-16 w-16 items-center justify-center rounded-full bg-secondary text-muted-foreground shadow-[0_0_30px_oklch(var(--secondary)/0.5)]">
//         <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>
//       </div>
//       <h3 className="text-base font-bold tracking-tight text-foreground">{title}</h3>
//       {description && (
//         <p className="mx-auto mt-2 max-w-sm text-sm leading-relaxed text-muted-foreground">
//           {description}
//         </p>
//       )}
//       {action && <div className="mt-6 flex justify-center">{action}</div>}
//     </div>
//   );
// }

// // -- Data display ---------------------------------------------------------

// export function Stat({
//   label, value, hint, tone,
// }: { label: string; value: ReactNode; hint?: ReactNode; tone?: "good" | "warn" | "bad" }) {
//   const toneClass =
//     tone === "good" ? "text-emerald-600 dark:text-emerald-400 drop-shadow-[0_0_8px_rgba(16,185,129,0.2)]"
//     : tone === "warn" ? "text-amber-600 dark:text-amber-400 drop-shadow-[0_0_8px_rgba(245,158,11,0.2)]"
//     : tone === "bad" ? "text-destructive dark:text-red-400 drop-shadow-[0_0_8px_rgba(239,68,68,0.2)]"
//     : "text-foreground";
    
//   return (
//     <div className="min-w-0 flex flex-col justify-center">
//       <dt className="text-[10px] font-bold uppercase tracking-[0.15em] text-muted-foreground mb-2">
//         {label}
//       </dt>
//       <dd className={cx("truncate text-4xl font-extrabold tracking-tighter tabular-nums", toneClass)}>
//         {value}
//       </dd>
//       {hint && <p className="mt-2 text-xs font-medium text-muted-foreground/80">{hint}</p>}
//     </div>
//   );
// }

// export function TableWrap({ children }: { children: ReactNode }) {
//   return (
//     <div className="overflow-hidden rounded-[16px] border border-border shadow-sm bg-card transition-colors duration-500">
//       <div className="overflow-x-auto scroll-x">{children}</div>
//     </div>
//   );
// }

// export function Table({ children, ariaLabel }: { children: ReactNode; ariaLabel?: string }) {
//   return (
//     <table aria-label={ariaLabel} className="w-full min-w-[40rem] border-collapse text-left text-sm">
//       {children}
//     </table>
//   );
// }

// export function Th({ children, className }: { children?: ReactNode; className?: string }) {
//   return (
//     <th scope="col" className={cx(
//       "border-b border-border/80 bg-secondary/30 px-5 py-3.5 text-[10px] font-bold uppercase tracking-widest text-muted-foreground whitespace-nowrap",
//       className,
//     )}>
//       {children}
//     </th>
//   );
// }

// export function Td({ children, className }: { children: ReactNode; className?: string }) {
//   return (
//     <td className={cx(
//       "border-b border-border/40 px-5 py-4 align-middle text-foreground transition-colors group-hover:bg-muted/40",
//       className,
//     )}>
//       {children}
//     </td>
//   );
// }

// export function Mono({ children, className, title }: { children: ReactNode; className?: string; title?: string }) {
//   return (
//     <span title={title} className={cx("font-mono text-[11px] tracking-wider text-primary bg-primary/10 px-2 py-0.5 rounded-md border border-primary/20", className)}>
//       {children}
//     </span>
//   );
// }

// export function CopyButton({ value, label }: { value: string; label?: string }) {
//   const [copied, setCopied] = useState(false);
//   const timer = useRef<number>();
//   useEffect(() => () => window.clearTimeout(timer.current), []);

//   return (
//     <button
//       type="button"
//       onClick={() => {
//         navigator.clipboard?.writeText(value).then(() => {
//           setCopied(true);
//           timer.current = window.setTimeout(() => setCopied(false), 1400);
//         });
//       }}
//       aria-label={label ? `Copy ${label}` : "Copy"}
//       className="rounded-md bg-secondary px-2.5 py-1 text-[10px] font-bold uppercase tracking-wider text-secondary-foreground transition-all hover:bg-primary hover:text-primary-foreground focus-visible:outline focus-visible:outline-2 focus-visible:outline-ring active:scale-95"
//     >
//       {copied ? "Copied" : "Copy"}
//     </button>
//   );
// }

// export function Disclosure({
//   summary, children, defaultOpen,
// }: { summary: ReactNode; children: ReactNode; defaultOpen?: boolean }) {
//   return (
//     <details open={defaultOpen} className="group overflow-hidden rounded-xl border border-border bg-card transition-all duration-300 open:shadow-md">
//       <summary className="cursor-pointer list-none bg-secondary/20 px-5 py-4 text-xs font-bold uppercase tracking-wider text-foreground transition-colors hover:bg-secondary/50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-ring">
//         <span className="mr-3 inline-block text-primary transition-transform duration-300 group-open:rotate-90 drop-shadow-[0_0_5px_oklch(var(--primary)/0.5)]" aria-hidden="true">▶</span>
//         {summary}
//       </summary>
//       <div className="animate-in p-5 text-sm duration-300 border-t border-border/50 bg-background/50">
//         {children}
//       </div>
//     </details>
//   );
// }

import { useEffect, useRef, useState, type ReactNode } from "react";
import { ApiError } from "@/lib/api";
import { cx } from "@/lib/format";

// -- Layout ---------------------------------------------------------------

/** Premium Bento Glow Card from ATLAS */
export function Card({
  children, className, as: Tag = "section",
}: { children: ReactNode; className?: string; as?: "section" | "div" | "article" }) {
  return (
    <Tag className={cx(
      "relative overflow-hidden rounded-[16px] border border-border bg-card text-card-foreground shadow-sm group",
      "transition-all duration-500 ease-out hover:-translate-y-1 hover:border-primary/40",
      "hover:shadow-[0_8px_30px_rgba(0,0,0,0.06)] dark:hover:shadow-[0_8px_30px_rgba(0,0,0,0.4)]",
      className,
    )}>
      {/* Top Edge Sweep Glow Reveal */}
      <div className="absolute top-0 left-0 h-[1px] w-[200%] bg-gradient-to-r from-transparent via-primary to-transparent opacity-0 -translate-x-full transition-all duration-700 ease-out group-hover:translate-x-0 group-hover:opacity-100" />
      
      {/* Ambient Internal Corner Glow */}
      <div className="pointer-events-none absolute -left-20 -top-20 h-48 w-48 rounded-full bg-primary blur-[80px] transition-opacity duration-700 opacity-0 group-hover:opacity-[0.15]" />
      
      <div className="relative z-10">{children}</div>
    </Tag>
  );
}

export function CardHeader({
  title, subtitle, actions, id,
}: { title: ReactNode; subtitle?: ReactNode; actions?: ReactNode; id?: string }) {
  return (
    <div className="flex flex-wrap items-start justify-between gap-3 border-b border-border/50 px-6 py-5 bg-gradient-to-b from-primary/5 to-transparent">
      <div className="min-w-0">
        <h2 id={id} className="text-sm font-extrabold tracking-tight text-foreground drop-shadow-sm">{title}</h2>
        {subtitle && (
          <p className="mt-1.5 text-xs font-medium leading-relaxed text-muted-foreground">{subtitle}</p>
        )}
      </div>
      {actions && <div className="flex shrink-0 items-center gap-3">{actions}</div>}
    </div>
  );
}

export function CardBody({ children, className }: { children: ReactNode; className?: string }) {
  return <div className={cx("p-6", className)}>{children}</div>;
}

export function PageHeader({
  eyebrow, title, description, actions,
}: { eyebrow?: string; title: string; description?: ReactNode; actions?: ReactNode }) {
  return (
    <header className="mb-10 flex flex-col md:flex-row md:items-end justify-between gap-8 animate-in relative border-b border-border/50 pb-8">
      {/* Subtle Background Wash Behind Header */}
      <div className="absolute top-0 left-0 w-96 h-64 bg-primary opacity-[0.04] blur-[100px] pointer-events-none" />
      
      <div className="relative z-10 min-w-0 flex-1">
        {eyebrow && (
          <div className="flex items-center gap-3 mb-4">
             <span className="w-1.5 h-1.5 rounded-full bg-primary shadow-[0_0_8px_var(--color-primary)] animate-pulse" />
             <p className="text-[10px] font-bold uppercase tracking-[0.25em] text-primary drop-shadow-[0_0_8px_oklch(var(--primary)/0.3)]">
               {eyebrow}
             </p>
          </div>
        )}
        <h1 className="text-4xl md:text-5xl font-extrabold tracking-tight text-foreground sm:text-5xl bg-clip-text">
          {title}
        </h1>
        {description && (
          <div className="mt-4 max-w-3xl text-sm md:text-base font-medium leading-relaxed text-muted-foreground text-balance">
            {description}
          </div>
        )}
      </div>
      {actions && <div className="relative z-10 flex shrink-0 flex-wrap items-center gap-3">{actions}</div>}
    </header>
  );
}

// -- Controls -------------------------------------------------------------

type ButtonVariant = "primary" | "secondary" | "ghost" | "danger";

const BUTTON_VARIANTS: Record<ButtonVariant, string> = {
  primary: "group relative overflow-hidden bg-primary text-primary-foreground shadow-[0_4px_20px_oklch(var(--primary)/0.3)] hover:shadow-[0_0_30px_oklch(var(--primary)/0.4)] border border-transparent hover:border-primary-foreground/20",
  secondary: "bg-secondary text-secondary-foreground hover:bg-secondary/80 border border-border/60 shadow-sm",
  ghost: "text-foreground hover:bg-accent hover:text-accent-foreground border border-transparent",
  danger: "bg-destructive text-destructive-foreground hover:brightness-110 shadow-sm border border-transparent",
};

export function Button({
  children, onClick, variant = "secondary", disabled, pending, type = "button",
  className, title, size = "md",
}: {
  children: ReactNode; onClick?: () => void; variant?: ButtonVariant;
  disabled?: boolean; pending?: boolean; type?: "button" | "submit";
  className?: string; title?: string; size?: "sm" | "md";
}) {
  return (
    <button
      type={type} onClick={onClick} title={title}
      disabled={disabled || pending} aria-busy={pending || undefined}
      className={cx(
        "inline-flex items-center justify-center gap-2 rounded-xl font-bold transition-all duration-300",
        "active:scale-[0.98] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring",
        "disabled:cursor-not-allowed disabled:opacity-50 disabled:active:scale-100 disabled:hover:brightness-100 disabled:shadow-none",
        size === "sm" ? "px-4 py-2 text-xs" : "px-6 py-3.5 text-sm",
        BUTTON_VARIANTS[variant], className,
      )}
    >
      {variant === "primary" && (
        <div className="absolute top-0 left-0 h-full w-[200%] -translate-x-full bg-gradient-to-r from-transparent via-primary-foreground/20 to-transparent transition-all duration-1000 ease-out group-hover:translate-x-full group-hover:animate-sweep pointer-events-none" />
      )}
      <span className="relative z-10 flex items-center gap-2">
        {pending && <Spinner className="h-3.5 w-3.5" />}
        {children}
      </span>
    </button>
  );
}

export function Field({
  label, hint, children, htmlFor, required,
}: { label: string; hint?: ReactNode; children: ReactNode; htmlFor?: string; required?: boolean }) {
  return (
    <div className="space-y-2">
      <label htmlFor={htmlFor} className="block text-[11px] font-bold uppercase tracking-[0.1em] text-foreground/80">
        {label} {required && <span className="ml-1 text-destructive drop-shadow-[0_0_4px_oklch(var(--destructive)/0.5)]" aria-hidden="true">*</span>}
      </label>
      {children}
      {hint && <p className="text-[11px] font-medium leading-relaxed text-muted-foreground/90">{hint}</p>}
    </div>
  );
}

const CONTROL_CLASS =
  "w-full rounded-xl border border-input bg-background px-4 py-3 text-sm font-medium text-foreground transition-all duration-300 " +
  "placeholder:text-muted-foreground/70 focus:border-ring focus:bg-background focus:outline-none focus:ring-4 focus:ring-ring/15 " +
  "hover:border-border/80 hover:bg-secondary/10 disabled:cursor-not-allowed disabled:opacity-50";

export function TextInput(props: {
  id?: string; value: string; onChange: (v: string) => void; placeholder?: string;
  mono?: boolean; disabled?: boolean; type?: string; ariaLabel?: string;
}) {
  return (
    <input
      id={props.id} type={props.type ?? "text"} value={props.value}
      aria-label={props.ariaLabel} disabled={props.disabled} placeholder={props.placeholder}
      onChange={(e) => props.onChange(e.target.value)}
      className={cx(CONTROL_CLASS, props.mono && "font-mono text-xs tracking-wider shadow-inner")}
    />
  );
}

export function TextArea(props: {
  id?: string; value: string; onChange: (v: string) => void; placeholder?: string; rows?: number;
}) {
  return (
    <textarea
      id={props.id} rows={props.rows ?? 3} value={props.value}
      placeholder={props.placeholder} onChange={(e) => props.onChange(e.target.value)}
      className={cx(CONTROL_CLASS, "resize-y leading-relaxed shadow-inner")}
    />
  );
}

export function Select<T extends string>(props: {
  id?: string; value: T; onChange: (v: T) => void;
  options: { value: T; label: string }[]; ariaLabel?: string;
}) {
  return (
    <select
      id={props.id} value={props.value} aria-label={props.ariaLabel}
      onChange={(e) => props.onChange(e.target.value as T)}
      className={cx(CONTROL_CLASS, "shadow-inner cursor-pointer")}
    >
      {props.options.map((o) => (
        <option key={o.value} value={o.value}>{o.label}</option>
      ))}
    </select>
  );
}

// -- Status & Polish ------------------------------------------------------

export function Badge({
  children, className, title,
}: { children: ReactNode; className?: string; title?: string }) {
  return (
    <span
      title={title}
      className={cx(
        "inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-[10px] font-extrabold uppercase tracking-[0.1em]",
        "border border-primary/20 bg-primary/10 text-primary shadow-[0_2px_10px_oklch(var(--primary)/0.05)]",
        className,
      )}
    >
      {children}
    </span>
  );
}

export function Spinner({ className }: { className?: string }) {
  return (
    <svg className={cx("animate-spin drop-shadow-md", className ?? "h-4 w-4")} viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
      <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
    </svg>
  );
}

export function Skeleton({ className }: { className?: string }) {
  return (
    <div className={cx(
      "relative overflow-hidden rounded-xl bg-gradient-to-r from-secondary/50 via-secondary/30 to-secondary/50",
      className ?? "h-4 w-full",
    )}>
      <div className="absolute inset-0 -translate-x-full animate-shimmer bg-gradient-to-r from-transparent via-foreground/5 to-transparent" />
    </div>
  );
}

export function Async<T>({
  state, children, empty, isEmpty, skeleton, label,
}: {
  state: { data: T | null; error: Error | null; loading: boolean; reload: () => void };
  children: (data: T) => ReactNode; empty?: ReactNode; isEmpty?: (data: T) => boolean;
  skeleton?: ReactNode; label?: string;
}) {
  if (state.loading && state.data === null) {
    return (
      <div role="status" aria-live="polite" aria-label={label ? `Loading ${label}` : "Loading"}>
        {skeleton ?? (
          <div className="space-y-4 p-4 animate-in">
            <Skeleton className="h-4 w-1/3" />
            <Skeleton className="h-4 w-2/3" />
            <Skeleton className="h-4 w-1/2" />
          </div>
        )}
      </div>
    );
  }
  if (state.error) return <ErrorState error={state.error} onRetry={state.reload} />;
  if (state.data === null) return null;
  if (isEmpty?.(state.data)) {
    return <>{empty ?? <EmptyState title="No records found" />}</>;
  }
  return <div className="animate-in">{children(state.data)}</div>;
}

export function ErrorState({ error, onRetry }: { error: Error; onRetry?: () => void }) {
  const api = error instanceof ApiError ? error : null;
  const isAuth = api?.isAuthError ?? false;
  const offline = api?.status === 0;

  return (
    <div role="alert" className="flex flex-col items-center justify-center py-20 text-center animate-in px-6">
      <div className={cx(
        "text-5xl mb-6 filter drop-shadow-[0_0_20px_currentColor]",
        isAuth ? "text-amber-500 opacity-90" : "text-destructive opacity-80"
      )}>⚠</div>
      <div className="font-extrabold tracking-tight text-xl text-foreground">
        {offline ? "Network Interruption" : isAuth ? "Access Restricted" : api?.isNotFound ? "Record Not Found" : "System Interruption"}
      </div>
      <p className="mt-3 max-w-md text-sm font-medium leading-relaxed text-muted-foreground/90">{error.message}</p>
      
      {isAuth && (
        <p className="mt-4 max-w-md text-xs leading-relaxed text-muted-foreground/70">
          Case routes are party-scoped. Ensure you are signed in as the filing member, the merchant, or an authorized reviewer.
        </p>
      )}
      
      {onRetry && !isAuth && (
        <div className="mt-8">
          <Button onClick={onRetry} variant="secondary" className="hover:border-primary hover:shadow-[0_0_15px_oklch(var(--primary)/0.2)]">Restore Connection</Button>
        </div>
      )}
    </div>
  );
}

export function EmptyState({
  title, description, action,
}: { title: string; description?: ReactNode; action?: ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center rounded-[20px] border border-dashed border-border/80 bg-gradient-to-b from-card/40 to-transparent px-8 py-20 text-center transition-all duration-700 hover:border-primary/40 hover:bg-card/60 hover:shadow-[0_10px_40px_oklch(var(--primary)/0.05)] animate-in group">
      <div className="mb-6 flex h-20 w-20 items-center justify-center rounded-full bg-secondary/80 text-muted-foreground shadow-[0_0_40px_oklch(var(--secondary)/0.8)] transition-transform duration-500 group-hover:scale-110 group-hover:text-primary group-hover:bg-primary/10">
        <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>
      </div>
      <h3 className="text-lg font-extrabold tracking-tight text-foreground drop-shadow-sm">{title}</h3>
      {description && (
        <p className="mx-auto mt-3 max-w-sm text-sm font-medium leading-relaxed text-muted-foreground">
          {description}
        </p>
      )}
      {action && <div className="mt-8 flex justify-center">{action}</div>}
    </div>
  );
}

// -- Data display ---------------------------------------------------------

export function Stat({
  label, value, hint, tone,
}: { label: string; value: ReactNode; hint?: ReactNode; tone?: "good" | "warn" | "bad" }) {
  const toneClass =
    tone === "good" ? "text-emerald-500 drop-shadow-[0_0_12px_rgba(16,185,129,0.3)]"
    : tone === "warn" ? "text-amber-500 drop-shadow-[0_0_12px_rgba(245,158,11,0.3)]"
    : tone === "bad" ? "text-destructive drop-shadow-[0_0_12px_oklch(var(--destructive)/0.3)]"
    : "text-transparent bg-clip-text bg-gradient-to-b from-foreground to-foreground/60";
    
  return (
    <div className="min-w-0 flex flex-col justify-center relative group">
      <div className="absolute -inset-4 bg-primary/0 blur-xl transition-colors duration-500 group-hover:bg-primary/5 rounded-full pointer-events-none" />
      <dt className="relative z-10 flex items-center gap-2 mb-3">
        {tone && <span className={cx("w-1.5 h-1.5 rounded-full shadow-[0_0_8px_currentColor]", toneClass.split(" ")[0])} />}
        <span className="text-[10px] font-bold uppercase tracking-[0.2em] text-muted-foreground">{label}</span>
      </dt>
      <dd className={cx("relative z-10 truncate text-5xl md:text-6xl font-extrabold tracking-tighter tabular-nums", toneClass)}>
        {value}
      </dd>
      {hint && <p className="relative z-10 mt-3 text-xs font-semibold uppercase tracking-wider text-muted-foreground/70">{hint}</p>}
    </div>
  );
}

export function TableWrap({ children }: { children: ReactNode }) {
  return (
    <div className="overflow-hidden rounded-[16px] border border-border shadow-md bg-card transition-colors duration-500 group hover:border-primary/30">
      <div className="overflow-x-auto scroll-x">{children}</div>
    </div>
  );
}

export function Table({ children, ariaLabel }: { children: ReactNode; ariaLabel?: string }) {
  return (
    <table aria-label={ariaLabel} className="w-full min-w-[40rem] border-collapse text-left text-sm">
      {children}
    </table>
  );
}

export function Th({ children, className }: { children?: ReactNode; className?: string }) {
  return (
    <th scope="col" className={cx(
      "border-b border-border bg-gradient-to-b from-secondary/40 to-secondary/10 px-5 py-4 text-[10px] font-extrabold uppercase tracking-[0.15em] text-muted-foreground whitespace-nowrap backdrop-blur-sm",
      className,
    )}>
      {children}
    </th>
  );
}

export function Td({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <td className={cx(
      "border-b border-border/40 px-5 py-4 align-middle font-medium text-foreground transition-all duration-300 group-hover:bg-secondary/40",
      className,
    )}>
      {children}
    </td>
  );
}

export function Mono({ children, className, title }: { children: ReactNode; className?: string; title?: string }) {
  return (
    <span title={title} className={cx("font-mono text-[11px] font-bold tracking-[0.08em] text-primary bg-primary/10 px-2 py-0.5 rounded-md border border-primary/20 shadow-sm", className)}>
      {children}
    </span>
  );
}

export function CopyButton({ value, label }: { value: string; label?: string }) {
  const [copied, setCopied] = useState(false);
  const timer = useRef<number>();
  useEffect(() => () => window.clearTimeout(timer.current), []);

  return (
    <button
      type="button"
      onClick={() => {
        navigator.clipboard?.writeText(value).then(() => {
          setCopied(true);
          timer.current = window.setTimeout(() => setCopied(false), 1400);
        });
      }}
      aria-label={label ? `Copy ${label}` : "Copy"}
      className="relative overflow-hidden rounded-md bg-secondary/80 px-3 py-1 text-[10px] font-extrabold uppercase tracking-[0.15em] text-foreground border border-border shadow-sm transition-all duration-300 hover:bg-primary hover:text-primary-foreground hover:border-primary hover:shadow-[0_0_15px_oklch(var(--primary)/0.3)] focus-visible:outline focus-visible:outline-2 focus-visible:outline-ring active:scale-95"
    >
      <span className="relative z-10">{copied ? "Copied!" : "Copy"}</span>
    </button>
  );
}

export function Disclosure({
  summary, children, defaultOpen,
}: { summary: ReactNode; children: ReactNode; defaultOpen?: boolean }) {
  return (
    <details open={defaultOpen} className="group overflow-hidden rounded-[14px] border border-border bg-card transition-all duration-500 hover:border-primary/40 open:shadow-[0_8px_30px_rgba(0,0,0,0.06)] dark:open:shadow-[0_8px_30px_rgba(0,0,0,0.3)]">
      <summary className="cursor-pointer list-none bg-gradient-to-r from-secondary/30 to-transparent px-5 py-4 text-xs font-extrabold uppercase tracking-wider text-foreground transition-colors hover:bg-secondary/50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-ring">
        <span className="mr-3 inline-block text-primary transition-transform duration-500 group-open:rotate-90 drop-shadow-[0_0_8px_oklch(var(--primary)/0.5)]" aria-hidden="true">▶</span>
        {summary}
      </summary>
      <div className="animate-in fade-in slide-in-from-top-4 p-5 text-sm duration-500 ease-out border-t border-border/50 bg-background/30 backdrop-blur-sm">
        {children}
      </div>
    </details>
  );
}