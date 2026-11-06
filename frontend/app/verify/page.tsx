/**
 * app/verify/page.tsx — Where a confirmation link lands.
 *
 * A Server Component whose only job is the boundary. Reading the query string is a client-side thing, and
 * under Cache Components a route that reads it without a fallback cannot prerender at all — the build fails
 * rather than serving the page. The `<Suspense>` fallback is the static shell, and it is the same card the
 * client ends up replacing, so the visitor never sees a blank page between the inbox and the answer.
 */

import { Suspense } from "react";

import VerifyNotice from "./verify-notice";
import VerifyToken from "./verify-token";

export default function VerifyPage() {
  return (
    <Suspense fallback={<VerifyNotice phase="working" />}>
      <VerifyToken />
    </Suspense>
  );
}
