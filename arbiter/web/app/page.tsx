export default function Home() {
  return (
    <div className="mx-auto max-w-2xl space-y-4 p-10">
      <h1 className="text-2xl font-bold">ARBITER</h1>
      <p className="text-neutral-600 dark:text-neutral-400">
        Auditable dispute adjudication. Open a case directly:
      </p>
      <ul className="list-inside list-disc space-y-1 text-sm">
        <li>
          <code className="rounded bg-neutral-100 px-1 dark:bg-neutral-800">/merchant/&lt;caseId&gt;</code> —
          merchant console (proof tree, evidence, counterfactuals)
        </li>
        <li>
          <code className="rounded bg-neutral-100 px-1 dark:bg-neutral-800">/cardmember/&lt;caseId&gt;</code> —
          card member portal
        </li>
        <li>
          <code className="rounded bg-neutral-100 px-1 dark:bg-neutral-800">/review/&lt;caseId&gt;</code> —
          escalated-case human review
        </li>
        <li>
          <code className="rounded bg-neutral-100 px-1 dark:bg-neutral-800">/fairness</code> — rule-level
          disparate-impact dashboard
        </li>
      </ul>
    </div>
  );
}
