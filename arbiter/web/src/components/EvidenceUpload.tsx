import { useRef, useState } from "react";
import { api } from "@/lib/api";
import { bytes, cx, percent } from "@/lib/format";
import type { HealthResponse, UploadResponse } from "@/lib/types";
import { useApi } from "@/lib/useApi";
import { Badge, Button, ErrorState } from "./ui";

/**
 * Evidence submission.
 *
 * The client-side checks here deliberately mirror the server's rather than
 * replacing them — the server sniffs magic bytes and enforces the size cap
 * and per-case ceiling regardless of what this form does. Checking here
 * too just means a user learns their 40 MB scan is too large before
 * spending a minute uploading it, not after.
 *
 * What comes back is worth surfacing in full: the scan verdict, the
 * extraction method actually used (born-digital PDF text, OCR, or the
 * VLM), and any forensic findings. A merchant who uploads a document whose
 * ModDate postdates the dispute filing should see that ARBITER noticed.
 */

const ACCEPTED = ["application/pdf", "image/png", "image/jpeg"];

/** Used only until `/health` answers. The cap is server configuration
 *  (`ARBITER_MAX_ARTIFACT_BYTES`); hardcoding it as fact meant a deployment
 *  that raised or lowered it had a console rejecting files the API would
 *  have taken, or promising a size it would refuse. */
const FALLBACK_MAX_BYTES = 25 * 1024 * 1024;

export default function EvidenceUpload({
  caseId, onUploaded,
}: { caseId: string; onUploaded: () => void }) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [dragging, setDragging] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<Error | null>(null);
  const [result, setResult] = useState<UploadResponse | null>(null);

  // The client check mirrors the server's rather than replacing it — the
  // server sniffs magic bytes and enforces the cap regardless. Checking here
  // just means a user learns their scan is too large before spending a
  // minute uploading it. Mirroring is only useful if it mirrors the real
  // number, which is why it is fetched.
  const health = useApi<HealthResponse>(() => api.health(), []);
  const maxBytes = health.data?.limits?.max_artifact_bytes ?? FALLBACK_MAX_BYTES;
  const maxPerCase = health.data?.limits?.max_artifacts_per_case;

  function choose(candidate: File | null) {
    setResult(null);
    setError(null);
    if (!candidate) { setFile(null); return; }
    if (candidate.size > maxBytes) {
      setError(new Error(
        `${candidate.name} is ${bytes(candidate.size)}. The limit is ${bytes(maxBytes)}.`,
      ));
      setFile(null);
      return;
    }
    setFile(candidate);
  }

  async function upload() {
    if (!file) return;
    setPending(true);
    setError(null);
    try {
      const response = await api.uploadEvidence(caseId, file);
      setResult(response);
      setFile(null);
      if (inputRef.current) inputRef.current.value = "";
      onUploaded();
    } catch (caught) {
      setError(caught instanceof Error ? caught : new Error(String(caught)));
    } finally {
      setPending(false);
    }
  }

  const scan = (result?.report?.["scan"] ?? {}) as { reason?: string; sniffed_mime_type?: string };
  const forensics = (result?.report?.["forensics"] ?? null) as
    | { findings?: string[]; confidence_penalty?: number }
    | null;
  const method = result?.report?.["extraction_method"] as string | null | undefined;

  return (
    <div className="space-y-3">
      <div
        onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragging(false);
          choose(e.dataTransfer.files[0] ?? null);
        }}
        className={cx(
          "rounded-xl border-2 border-dashed px-4 py-6 text-center transition-colors",
          dragging
            ? "border-brand-500 bg-brand-50 dark:bg-brand-950/40"
            : "border-neutral-300 dark:border-neutral-700",
        )}
      >
        <input
          ref={inputRef}
          id={`evidence-file-${caseId}`}
          type="file"
          className="sr-only"
          accept={ACCEPTED.join(",")}
          onChange={(e) => choose(e.target.files?.[0] ?? null)}
        />
        <label
          htmlFor={`evidence-file-${caseId}`}
          className="cursor-pointer text-sm font-medium text-brand-700 hover:underline dark:text-brand-400"
        >
          Choose a file
        </label>
        <span className="text-sm text-neutral-600 dark:text-neutral-400"> or drag it here</span>
        <p className="mt-1.5 text-2xs text-neutral-500 dark:text-neutral-500">
          PDF, PNG, or JPEG up to {bytes(maxBytes)}
          {maxPerCase !== undefined && <>, {maxPerCase} documents per case</>}. The file type is
          determined from its magic bytes on the server, never from its name or Content-Type.
        </p>
      </div>

      {file && (
        <div className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-neutral-200 px-3 py-2 dark:border-neutral-800">
          <div className="min-w-0">
            <p className="truncate text-xs font-medium text-neutral-800 dark:text-neutral-200">{file.name}</p>
            <p className="text-2xs text-neutral-500">{bytes(file.size)} · {file.type || "unknown type"}</p>
          </div>
          <div className="flex gap-2">
            <Button size="sm" variant="ghost" onClick={() => choose(null)}>Remove</Button>
            <Button size="sm" variant="primary" pending={pending} onClick={upload}>Upload</Button>
          </div>
        </div>
      )}

      {error && <ErrorState error={error} />}

      {/* Three outcomes, not two. A structurally valid document that yields
          no extractable fields is STORED and retained under Reg E — calling
          that "rejected" told the user their evidence had been refused when
          the system had in fact kept it, and contradicted both the scan
          result in the same payload and the artifact row just written. */}
      {result && (() => {
        const stored = result.accepted;
        const extracted = result.evidence_extracted !== false;
        const tone = !stored ? "bad" : extracted ? "good" : "warn";
        return (
        <div className={cx(
          "rounded-lg border p-3",
          tone === "good"
            ? "border-emerald-300 bg-emerald-50 dark:border-emerald-900 dark:bg-emerald-950/40"
            : tone === "warn"
            ? "border-amber-300 bg-amber-50 dark:border-amber-900 dark:bg-amber-950/40"
            : "border-red-300 bg-red-50 dark:border-red-900 dark:bg-red-950/40",
        )}>
          <div className="flex flex-wrap items-center gap-2">
            <Badge className={tone === "good"
              ? "bg-emerald-100 text-emerald-800 dark:bg-emerald-900 dark:text-emerald-200"
              : tone === "warn"
              ? "bg-amber-100 text-amber-900 dark:bg-amber-900 dark:text-amber-200"
              : "bg-red-100 text-red-800 dark:bg-red-900 dark:text-red-200"}>
              {tone === "good" ? "accepted" : tone === "warn" ? "stored — nothing extracted" : "rejected"}
            </Badge>
            {scan.sniffed_mime_type && <Badge>sniffed {scan.sniffed_mime_type}</Badge>}
            {method && <Badge title="Born-digital PDF text is checked first — it is ~8x faster than OCR and free latency when it works.">
              extracted via {method}
            </Badge>}
          </div>

          {!stored && scan.reason && (
            <p className="mt-2 text-xs text-red-800 dark:text-red-300">{scan.reason}</p>
          )}

          {stored && extracted && (
            <p className="mt-2 text-xs leading-relaxed text-emerald-800 dark:text-emerald-300">
              Stored and retained — Reg E 12 CFR 1005.11(d)(1) entitles the card member to request
              the documents relied on, so the bytes are kept and re-hashed on every read.
              This evidence enters at SUBMITTED tier; re-adjudicate to fold it into the decision.
            </p>
          )}

          {stored && !extracted && (
            <p className="mt-2 text-xs leading-relaxed text-amber-800 dark:text-amber-300">
              The file passed the scan and <strong className="font-semibold">is retained</strong> —
              it appears under "Documents relied on" and can be downloaded. But nothing readable
              came out of it, so it establishes no fact and adds no node to the evidence graph.
              A scan with no recoverable text, or a document whose fields the extractor does not
              recognise, lands here. Re-upload a clearer copy if it was meant to prove something.
            </p>
          )}

          {forensics?.findings?.length ? (
            <div className="mt-2 border-t border-black/10 pt-2 dark:border-white/10">
              <p className="text-2xs font-semibold text-amber-800 dark:text-amber-300">
                Forensic findings
              </p>
              <ul className="mt-1 list-disc space-y-0.5 pl-4 text-2xs text-amber-800 dark:text-amber-300">
                {forensics.findings.map((finding, i) => <li key={i}>{finding}</li>)}
              </ul>
              {!!forensics.confidence_penalty && (
                <p className="mt-1 text-2xs text-amber-700 dark:text-amber-400">
                  Evidentiary weight reduced by {percent(forensics.confidence_penalty, 0)}. Forensics
                  are a signal for a human reviewer — they never flip a predicate on their own.
                </p>
              )}
            </div>
          ) : null}
        </div>
        );
      })()}
    </div>
  );
}
