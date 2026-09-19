import { NavLink } from "@/components/ui/NavLink";

// PRD order: Alerts, Stats, About. A fixed list, not a prop: the three primary-nav destinations
// are a property of this app, not something a caller configures (M8a review Q3/N4 — replaces the
// `NavLink as Link` alias and the two source-text nav tests it forced).
const PRIMARY_LINKS = [
  { href: "/alerts", label: "Alerts" },
  { href: "/stats", label: "Stats" },
  { href: "/about", label: "About" },
] as const;

/**
 * The dashboard's primary navigation: Alerts, Stats, About, each a `NavLink` (dashboard-wide link
 * treatment + `aria-current="page"`). Dumb component — no props, no data fetching.
 */
export function PrimaryNav() {
  return (
    <nav aria-label="Primary">
      <ul className="flex items-center gap-4 text-sm sm:gap-6">
        {PRIMARY_LINKS.map(({ href, label }) => (
          <li key={href}>
            <NavLink href={href}>{label}</NavLink>
          </li>
        ))}
      </ul>
    </nav>
  );
}
