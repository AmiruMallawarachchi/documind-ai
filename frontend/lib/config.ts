/**
 * lib/config.ts — Where the backend lives, in one place with no imports of its own.
 *
 * Split out of lib/api.ts because both api.ts and session.ts need the URL, and having session.ts import it
 * back from api.ts would make the two modules depend on each other.
 *
 * The URL is read as a literal `process.env.NEXT_PUBLIC_*` member expression rather than destructured:
 * Next only inlines that exact form into the client bundle.
 */

export const API_URL =
  process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
