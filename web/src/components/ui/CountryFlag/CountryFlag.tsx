import { countryFlag } from "@/lib/format";

import type { CountryFlagProps } from "./interface";

export function CountryFlag({ code }: CountryFlagProps) {
  const flag = countryFlag(code);
  if (flag === "") {
    return null;
  }

  const label = `Country ${code}`;
  return (
    <span role="img" aria-label={label} title={code ?? undefined}>
      {flag}
    </span>
  );
}
