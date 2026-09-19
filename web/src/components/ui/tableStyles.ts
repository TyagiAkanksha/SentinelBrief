// Shared table treatment (m8b task-08). DataTable owns the queue; CostTable and DistributionTable
// stay standalone because they use `<th scope="row">` rowheaders DataTable does not model — so the
// portfolio-grade frame/rule/hover/padding lives here once instead of as a copied class string.

/** Rounded, bordered surface frame; `overflow-hidden` clips the collapsed table to the radius. */
export const TABLE_FRAME = "overflow-hidden rounded-(--radius) border border-border";

/** Inner wrapper: lets a wide table scroll horizontally without breaking the frame. */
export const TABLE_SCROLL = "overflow-x-auto";

/** The table itself: row separators, hover, and consistent cell padding for the body. */
export const TABLE_ELEMENT =
  "w-full border-collapse text-left text-sm " +
  "[&_tbody_td]:px-3 [&_tbody_td]:py-2 [&_tbody_th]:px-3 [&_tbody_th]:py-2 " +
  "[&_tbody_tr]:border-t [&_tbody_tr]:border-border [&_tbody_tr:hover]:bg-surface-2";

/** Header cell: faint, small, upper-case labels with the bottom rule. */
export const TABLE_HEAD_CELL =
  "border-b border-border px-3 py-2 text-xs font-medium tracking-wide text-muted uppercase";

/** Visible caption used as a table title (CostTable/DistributionTable). */
export const TABLE_CAPTION = "caption-top pb-2 text-left text-sm font-medium text-text";

/** Right-aligned, tabular-nums treatment for numeric columns. */
export const CELL_NUMERIC = "text-right tabular-nums";
