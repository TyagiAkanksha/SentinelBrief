import { BUTTON_BASE, BUTTON_VARIANTS } from "./interface";
import type { ButtonProps } from "./interface";

/** Dumb button primitive: renders a native <button> with the shared shape and a colour variant. */
export function Button({ children, variant = "primary", type = "button", className }: ButtonProps) {
  return (
    <button
      type={type}
      className={`${BUTTON_BASE} ${BUTTON_VARIANTS[variant]}${className ? ` ${className}` : ""}`}
    >
      {children}
    </button>
  );
}
