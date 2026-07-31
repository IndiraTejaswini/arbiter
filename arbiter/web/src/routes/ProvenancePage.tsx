import { useState } from "react";
import {
  Async, Badge, Button, Card, CardBody, CardHeader, CopyButton, EmptyState,
  ErrorState, Field, Mono, PageHeader, Stat, TextInput,
} from "@/components/ui";
import { api } from "@/lib/api";
import { cx, dateTime } from "@/lib/format";
import { getSession } from "@/lib/session";
import type { ConsistencyProof, InclusionProof, SignedTreeHead } from "@/lib/types";
import { useAction, useApi } from "@/lib/useApi";

/**
 * ADEC — Ante-Dispute Evidence Commitments.
 *
 * The one property this buys, which nothing else in the dispute industry
 * has: **non-backdating is provable rather than assumed.** A delivery
 * confirmation produced after a chargeback is otherwise indistinguishable
 * from one produced at delivery — every incumbent system accepts
 * merchant-submitted PDFs at dispute time and simply hopes.
 *
 * This is a Certificate-Transparency-style log, deliberately not a
 * blockchain: blockchain solves Byzantine agreement among mutually
 * distrusting validators, and the actual requirement here is
 * non-repudiable ordering by a single accountable operator, verifiable by
 * external parties. CT solved exactly that in 2013 and secures the web PKI.
 */
export default function ProvenancePage() {
  const session = getSession();
  const sth = useApi<SignedTreeHead>(() => api.getLatestSth(), []);

  return (
    <>
      <PageHeader
        eyebrow="Provenance"
        title="Transparency log"
        description="Merchants commit a hash of an artifact at the moment the underlying event happens. Only the hash leaves the merchant; the artifact itself is revealed only if a dispute occurs, and only to Amex."
      />

      <div className="grid gap-5 lg:grid-cols-2">
        <Card>
          <CardHeader
            title="Latest signed tree head"
            subtitle="Ed25519-signed by the log operator and RFC-3161-timestamped. Anyone holding a prior tree head can demand a consistency proof that the log only ever grew."
            actions={<Button size="sm" onClick={sth.reload}>Refresh</Button>}
          />
          <CardBody>
            <Async
              state={sth}
              label="signed tree head"
              skeleton={<EmptyState title="Loading the log…" />}
            >
              {(head) => (
                <>
                  <dl className="grid grid-cols-2 gap-4">
                    <Stat label="Tree size" value={head.tree_size} hint="commitments logged" />
                    <Stat
                      label="Sealed at"
                      value={dateTime(new Date(head.timestamp_unix_ns / 1e6).toISOString())}
                      hint="TSA gen_time"
                    />
                  </dl>
                  <dl className="mt-4 space-y-2 border-t border-neutral-200 pt-4 dark:border-neutral-800">
                    <HashRow label="Root hash" value={head.root_hash} />
                    <HashRow label="Log id" value={head.log_id} />
                    <HashRow label="STH signature" value={head.signature} />
                    <HashRow label="TSA key id" value={head.tsa_token.tsa_key_id} />
                  </dl>
                  <p className="mt-4 text-2xs leading-relaxed text-neutral-500 dark:text-neutral-400">
                    The log operator and the timestamp authority are separate signing
                    identities by design: compromising one does not let an attacker forge
                    the other, so backdating requires breaking both.
                  </p>
                </>
              )}
            </Async>
          </CardBody>
        </Card>

        <Card>
          <CardHeader
            title="Consistency proof"
            subtitle="Prove the log at an earlier size is a prefix of the log now — the check that makes a split-view attack detectable."
          />
          <CardBody>
            <ConsistencyChecker currentSize={sth.data?.tree_size ?? null} />
          </CardBody>
        </Card>
      </div>

      <Card className="mt-5">
        <CardHeader
          title="Inclusion proof"
          subtitle="Verify that a specific commitment is in a signed root. A merchant may only inspect their own commitments; reviewers may inspect any."
        />
        <CardBody>
          <InclusionChecker />
        </CardBody>
      </Card>

      <Card className="mt-5">
        <CardHeader
          title="Dispute-time reveal"
          subtitle="Disclose artifact ‖ salt. ARBITER recomputes the hash locally — it never trusts a hash handed to it after the fact — and verifies inclusion in a root whose timestamp precedes the dispute filing."
        />
        <CardBody>
          <RevealChecker />
        </CardBody>
      </Card>

      {session?.role === "MERCHANT" && (
        <Card className="mt-5">
          <CardHeader
            title="Commit an artifact"
            subtitle="Post only the hash, at the moment the real-world event occurs. The merchant_id comes from your token — a commitment cannot be created on another merchant's behalf."
          />
          <CardBody>
            <CommitForm />
          </CardBody>
        </Card>
      )}
    </>
  );
}

function HashRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-baseline justify-between gap-2">
      <dt className="shrink-0 text-2xs font-medium uppercase tracking-wide text-neutral-500">{label}</dt>
      <dd className="flex min-w-0 items-center gap-1">
        <Mono className="truncate" title={value}>{value}</Mono>
        <CopyButton value={value} label={label} />
      </dd>
    </div>
  );
}

function ConsistencyChecker({ currentSize }: { currentSize: number | null }) {
  const [fromSize, setFromSize] = useState("");
  const check = useAction((from: number) => api.getConsistencyProof(from, currentSize ?? undefined));

  return (
    <div className="space-y-3">
      <form
        className="flex flex-wrap items-end gap-3"
        onSubmit={(e) => { e.preventDefault(); check.run(Number(fromSize)); }}
      >
        <div className="w-40">
          <Field label="Earlier tree size" htmlFor="from-size">
            <TextInput id="from-size" type="number" value={fromSize} onChange={setFromSize} placeholder="1" />
          </Field>
        </div>
        <Button type="submit" pending={check.pending} disabled={!fromSize}>Get proof</Button>
      </form>

      {check.error && <ErrorState error={check.error} />}
      {check.result && <ProofResult proof={check.result} />}
      {!check.result && !check.error && (
        <p className="text-2xs leading-relaxed text-neutral-500 dark:text-neutral-400">
          {currentSize === null
            ? "The log has no sealed tree head yet."
            : `Current tree size is ${currentSize}. Enter any earlier size to prove the log only appended.`}
        </p>
      )}
    </div>
  );
}

function ProofResult({ proof }: { proof: ConsistencyProof }) {
  return (
    <div className="rounded-lg border border-emerald-300 bg-emerald-50 p-3 dark:border-emerald-900 dark:bg-emerald-950/40">
      <p className="text-xs font-semibold text-emerald-900 dark:text-emerald-200">
        Consistency proof: size {proof.size1} → {proof.size2}
      </p>
      <p className="mt-1 text-2xs text-emerald-800 dark:text-emerald-300">
        {proof.proof.length} hash{proof.proof.length === 1 ? "" : "es"} — O(log n), independent of how
        many commitments the log holds.
      </p>
      <div className="mt-2 max-h-32 space-y-0.5 overflow-y-auto">
        {proof.proof.map((h, i) => (
          <Mono key={i} className="block truncate">{h}</Mono>
        ))}
      </div>
    </div>
  );
}

function InclusionChecker() {
  const [commitmentId, setCommitmentId] = useState("");
  const check = useAction((id: string) => api.getInclusionProof(id));

  return (
    <div className="space-y-3">
      <form
        className="flex flex-wrap items-end gap-3"
        onSubmit={(e) => { e.preventDefault(); check.run(commitmentId.trim()); }}
      >
        <div className="min-w-[20rem] flex-1">
          <Field label="Commitment id" htmlFor="commitment-id">
            <TextInput id="commitment-id" mono value={commitmentId} onChange={setCommitmentId}
                       placeholder="00000000-0000-0000-0000-000000000000" />
          </Field>
        </div>
        <Button type="submit" pending={check.pending} disabled={!commitmentId.trim()}>
          Verify inclusion
        </Button>
      </form>

      {check.error && <ErrorState error={check.error} />}
      {check.result && <InclusionResult proof={check.result} />}
    </div>
  );
}

function InclusionResult({ proof }: { proof: InclusionProof }) {
  return (
    <div className="rounded-lg border border-neutral-200 p-3 dark:border-neutral-800">
      <div className="flex flex-wrap items-center gap-2">
        <Badge className="bg-violet-100 text-violet-800 dark:bg-violet-950 dark:text-violet-300">
          leaf {proof.leaf_index}
        </Badge>
        <Badge>tree size {proof.tree_size}</Badge>
        <Badge>{proof.audit_path.length} audit-path hashes</Badge>
      </div>
      <dl className="mt-3 space-y-2">
        <HashRow label="Root" value={proof.sth.root_hash} />
        <HashRow label="STH signature" value={proof.sth.signature} />
      </dl>
      <p className="mt-3 text-2xs leading-relaxed text-neutral-500 dark:text-neutral-400">
        Sealed at {dateTime(new Date(proof.sth.timestamp_unix_ns / 1e6).toISOString())}. If that
        instant precedes the dispute filing, the artifact provably existed before the dispute did —
        which is the entire point.
      </p>
    </div>
  );
}

/**
 * Reveal and verify.
 *
 * Failure DEMOTES the claim to SUBMITTED tier; it never rejects it.
 * "Evidence degrades, never rejected" is a deliberate rule — a merchant
 * whose ADEC integration is broken should lose the higher trust weight,
 * not lose the ability to defend themselves at all.
 */
function RevealChecker() {
  const [commitmentId, setCommitmentId] = useState("");
  const [artifactHex, setArtifactHex] = useState("");
  const [saltHex, setSaltHex] = useState("");
  const [filedAt, setFiledAt] = useState("");

  const reveal = useAction(() =>
    api.revealCommitment(commitmentId.trim(), {
      artifact_hex: artifactHex.trim(),
      salt_hex: saltHex.trim(),
      dispute_filed_at: filedAt ? new Date(filedAt).toISOString() : null,
    }),
  );

  const ready = Boolean(commitmentId.trim() && artifactHex.trim() && saltHex.trim());

  return (
    <div className="space-y-4">
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Commitment id" htmlFor="reveal-id" required>
          <TextInput id="reveal-id" mono value={commitmentId} onChange={setCommitmentId}
                     placeholder="00000000-0000-0000-0000-000000000000" />
        </Field>
        <Field
          label="Dispute filed at"
          htmlFor="reveal-filed"
          hint="The deadline the commitment must predate. Leave blank to verify inclusion without a time bound."
        >
          <TextInput id="reveal-filed" type="datetime-local" value={filedAt} onChange={setFiledAt} />
        </Field>
        <Field label="Artifact (hex)" htmlFor="reveal-artifact" required>
          <TextInput id="reveal-artifact" mono value={artifactHex} onChange={setArtifactHex}
                     placeholder="hex-encoded artifact bytes" />
        </Field>
        <Field
          label="Salt (hex)"
          htmlFor="reveal-salt"
          required
          hint="Without a salt, a low-entropy artifact such as a bare `delivered: true` would be brute-forceable from its hash alone."
        >
          <TextInput id="reveal-salt" mono value={saltHex} onChange={setSaltHex} placeholder="hex-encoded salt" />
        </Field>
      </div>

      {reveal.error && <ErrorState error={reveal.error} />}

      {reveal.result && (
        <div className={cx(
          "rounded-lg border p-3",
          reveal.result.ok
            ? "border-violet-300 bg-violet-50 dark:border-violet-900 dark:bg-violet-950/40"
            : "border-amber-300 bg-amber-50 dark:border-amber-900 dark:bg-amber-950/40",
        )}>
          <div className="flex flex-wrap items-center gap-2">
            <Badge className={reveal.result.ok
              ? "bg-violet-100 text-violet-800 dark:bg-violet-900 dark:text-violet-200"
              : "bg-amber-100 text-amber-900 dark:bg-amber-900 dark:text-amber-200"}>
              tier {reveal.result.tier}
            </Badge>
            <Check ok={reveal.result.inclusion_valid} label="Merkle inclusion" />
            <Check ok={reveal.result.sth_signature_valid} label="log-operator signature" />
            <Check ok={reveal.result.tsa_signature_valid} label="TSA signature" />
            {reveal.result.predates_deadline !== null && (
              <Check ok={reveal.result.predates_deadline} label="predates the dispute" />
            )}
          </div>
          <p className="mt-2 text-2xs leading-relaxed text-neutral-600 dark:text-neutral-400">
            {reveal.result.ok
              ? "Verified. This artifact provably existed before the dispute was filed — non-backdating is proven here, not assumed."
              : "Verification failed, so the claim enters at SUBMITTED tier instead of COMMITTED. Evidence degrades; it is never rejected outright."}
          </p>
        </div>
      )}

      <Button variant="primary" pending={reveal.pending} disabled={!ready} onClick={() => reveal.run()}>
        Reveal and verify
      </Button>
    </div>
  );
}

function Check({ ok, label }: { ok: boolean; label: string }) {
  return (
    <Badge className={ok
      ? "bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300"
      : "bg-red-100 text-red-800 dark:bg-red-950 dark:text-red-300"}>
      <span aria-hidden="true">{ok ? "✓" : "✗"}</span> {label}
    </Badge>
  );
}

function CommitForm() {
  const [artifactType, setArtifactType] = useState("shipment");
  const [hash, setHash] = useState("");
  const [eventTime, setEventTime] = useState(() => new Date().toISOString().slice(0, 16));

  const commit = useAction(() =>
    api.createCommitment({
      artifact_type: artifactType.trim(),
      commitment_hash: hash.trim(),
      event_time: new Date(eventTime).toISOString(),
    }),
  );

  const validHash = /^[0-9a-fA-F]{64}$/.test(hash.trim());

  return (
    <div className="space-y-4">
      <div className="grid gap-4 sm:grid-cols-3">
        <Field label="Artifact type" htmlFor="artifact-type">
          <TextInput id="artifact-type" value={artifactType} onChange={setArtifactType} />
        </Field>
        <Field label="Event time" htmlFor="event-time"
               hint="Your claim about when the event happened. Never authoritative — the server stamps its own committed_at.">
          <TextInput id="event-time" type="datetime-local" value={eventTime} onChange={setEventTime} />
        </Field>
        <Field
          label="Commitment hash"
          htmlFor="commit-hash"
          hint="sha256(artifact ‖ salt), hex. The salt is what stops a low-entropy artifact being brute-forced from its hash."
        >
          <TextInput id="commit-hash" mono value={hash} onChange={setHash} placeholder="64 hex characters" />
        </Field>
      </div>

      {hash.trim() && !validHash && (
        <p className="text-2xs text-amber-700 dark:text-amber-400">
          A commitment hash must be exactly 64 hex characters (sha256). Anything else would put an
          unverifiable leaf in an append-only log.
        </p>
      )}

      {commit.error && <ErrorState error={commit.error} />}

      {commit.result && (
        <div className="rounded-lg border border-emerald-300 bg-emerald-50 p-3 dark:border-emerald-900 dark:bg-emerald-950/40">
          <p className="text-xs font-semibold text-emerald-900 dark:text-emerald-200">Committed</p>
          <dl className="mt-2 space-y-1.5">
            <HashRow label="Commitment id" value={commit.result.commitment_id} />
            <div className="flex items-baseline justify-between gap-2">
              <dt className="text-2xs font-medium uppercase tracking-wide text-neutral-500">Leaf index</dt>
              <dd className="text-xs tabular-nums text-neutral-800 dark:text-neutral-200">
                {commit.result.leaf_index}
              </dd>
            </div>
            <div className="flex items-baseline justify-between gap-2">
              <dt className="text-2xs font-medium uppercase tracking-wide text-neutral-500">Committed at</dt>
              <dd className="text-xs text-neutral-800 dark:text-neutral-200">
                {dateTime(commit.result.committed_at)}
              </dd>
            </div>
          </dl>
        </div>
      )}

      <Button
        variant="primary"
        pending={commit.pending}
        disabled={!validHash}
        onClick={() => commit.run()}
        className={cx(!validHash && "opacity-50")}
      >
        Post commitment
      </Button>
    </div>
  );
}
