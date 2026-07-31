import { Link, isRouteErrorResponse, useRouteError } from "react-router-dom";
import { Button, Card, CardBody } from "./ui";

/**
 * Route-level error boundary.
 *
 * Without one, a render throw blanks the entire page — which in a console
 * where someone is mid-review of a dispute is the worst possible failure
 * mode, because it looks identical to the application not existing.
 */
export default function ErrorBoundary() {
  const error = useRouteError();

  const title = isRouteErrorResponse(error)
    ? `${error.status} ${error.statusText}`
    : "Something broke while rendering this page";
  const detail = isRouteErrorResponse(error)
    ? error.data
    : error instanceof Error
    ? error.message
    : String(error);

  return (
    <div className="mx-auto max-w-2xl px-4 py-16">
      <Card>
        <CardBody className="space-y-4">
          <div>
            <h1 className="text-lg font-bold text-neutral-900 dark:text-neutral-50">{title}</h1>
            <p className="mt-2 text-sm text-neutral-600 dark:text-neutral-400">
              This is a client-side rendering failure, not a decision failure. No case state
              was changed — the adjudication record is append-only and unaffected by anything
              the browser does.
            </p>
          </div>

          <pre className="max-h-48 overflow-auto rounded-lg bg-neutral-100 p-3 text-2xs text-neutral-700 dark:bg-neutral-900 dark:text-neutral-300">
            {detail}
          </pre>

          <div className="flex gap-2">
            <Button variant="primary" onClick={() => window.location.reload()}>Reload</Button>
            <Link to="/">
              <Button>Back to overview</Button>
            </Link>
          </div>
        </CardBody>
      </Card>
    </div>
  );
}
