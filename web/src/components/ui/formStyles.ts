// Shared control treatment for native form fields (the alerts FilterBar). A style used across
// several <select>/<input> elements lives here once (docs/FRONTEND-CONVENTIONS.md §2/§4) instead
// of a copied class string per control.

/** The field itself: bordered, radius-rounded, small, with a token focus ring. */
export const FIELD_CONTROL =
  "rounded-(--radius) border border-border bg-bg px-2 py-1 text-sm text-text focus-visible:ring-2 focus-visible:ring-ring";

/** The stacked label above a field: small, medium-weight, muted. */
export const FIELD_LABEL = "text-xs font-medium text-muted";
