import { COUNTRY_CODE_RE, countryFlag } from "@/lib/format";

import type { CountryFlagProps } from "./interface";

export function CountryFlag({ code }: CountryFlagProps) {
  if (typeof code !== "string" || !COUNTRY_CODE_RE.test(code)) {
    return null;
  }

  return (
    <span role="img" aria-label={`Country ${code}`} title={code} className="mr-1">
      {countryFlag(code)}
    </span>
  );
}
