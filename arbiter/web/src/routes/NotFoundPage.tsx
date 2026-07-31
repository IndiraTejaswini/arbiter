import { Link } from "react-router-dom";
import { Button, Card, CardBody } from "@/components/ui";

export default function NotFoundPage() {
  return (
    <div className="mx-auto max-w-lg py-16">
      <Card>
        <CardBody className="space-y-4 text-center">
          <p className="text-3xl font-bold text-neutral-300 dark:text-neutral-700">404</p>
          <div>
            <h1 className="text-sm font-semibold text-neutral-900 dark:text-neutral-50">
              No such page
            </h1>
            <p className="mt-1 text-xs leading-relaxed text-neutral-600 dark:text-neutral-400">
              If you followed a case link, the case may exist but not be visible to you —
              case routes are party-scoped, and that is the authorization layer working
              rather than a broken link.
            </p>
          </div>
          <Link to="/"><Button variant="primary">Back to overview</Button></Link>
        </CardBody>
      </Card>
    </div>
  );
}
