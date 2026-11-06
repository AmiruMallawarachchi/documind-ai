import type { Metadata } from "next";

/**
 * app/verify/layout.tsx — The tab title for the link a visitor opens from their inbox.
 *
 * Metadata has to live in a Server Component, and this route's page is a client component because it spends
 * a token over POST. A layout is the smallest thing that can hold the title; "Confirm your email" in the tab
 * is also the cheapest reassurance that the link went where it said it would.
 */
export const metadata: Metadata = {
  title: "Confirm your email · DocuMind AI",
};

export default function VerifyLayout({ children }: LayoutProps<"/verify">) {
  return children;
}
