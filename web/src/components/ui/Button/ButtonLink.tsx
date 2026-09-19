import Link from "next/link";

import { BUTTON_BASE, BUTTON_VARIANTS } from "./interface";
import type { ButtonLinkProps } from "./interface";

/** Button-styled link: a Next <Link> for internal routes, a safe new-tab <a> when `external`. */
export function ButtonLink({
  href,
  children,
  variant = "primary",
  external = false,
  className,
}: ButtonLinkProps) {
  const cls = `${BUTTON_BASE} ${BUTTON_VARIANTS[variant]}${className ? ` ${className}` : ""}`;
  if (external) {
    return (
      <a href={href} target="_blank" rel="noreferrer" className={cls}>
        {children}
      </a>
    );
  }
  return (
    <Link href={href} className={cls}>
      {children}
    </Link>
  );
}
