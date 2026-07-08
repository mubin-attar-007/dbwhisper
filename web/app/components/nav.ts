import type { IconName } from "./Icon";
import { LINKS } from "./links";

/** Shared app navigation — consumed by both the desktop sidebar and the mobile drawer. */
export const NAV: { label: string; icon: IconName; href?: string; soon?: boolean }[] = [
  { label: "Console", icon: "spark", href: "/app" },
  { label: "Saved / Verified", icon: "star", href: "/app/training" },
  { label: "Schema", icon: "table", href: "/app/schema" },
  { label: "Databases", icon: "database", soon: true },
];

export const EXTERNAL: { label: string; icon: IconName; href: string }[] = [
  { label: "GitHub", icon: "github", href: LINKS.github },
  { label: "LinkedIn", icon: "linkedin", href: LINKS.linkedin },
  { label: "Hugging Face", icon: "huggingface", href: LINKS.huggingface },
  { label: "API docs", icon: "book", href: LINKS.apiDocs },
];
