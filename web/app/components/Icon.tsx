import type { ReactNode, SVGProps } from "react";

/** Small, dependency-free line-icon set (24x24 grid, currentColor stroke). CSP-safe. */
const PATHS: Record<string, ReactNode> = {
  plus: <path d="M12 5v14M5 12h14" />,
  history: (
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="M12 7v5l3 2" />
    </>
  ),
  database: (
    <>
      <ellipse cx="12" cy="5" rx="8" ry="3" />
      <path d="M4 5v14c0 1.66 3.58 3 8 3s8-1.34 8-3V5" />
      <path d="M4 12c0 1.66 3.58 3 8 3s8-1.34 8-3" />
    </>
  ),
  star: <path d="M12 3.5l2.6 5.27 5.82.85-4.21 4.1.99 5.79L12 17.27 6.8 20l1-5.79-4.22-4.1 5.82-.85z" />,
  table: (
    <>
      <rect x="3" y="4" width="18" height="16" rx="2" />
      <path d="M3 9h18M9 4v16" />
    </>
  ),
  book: <path d="M4 5.5A2.5 2.5 0 016.5 3H20v15H6.5A2.5 2.5 0 004 20.5zM4 20.5A2.5 2.5 0 016.5 18H20" />,
  github: (
    <path d="M9 19c-4 1.3-4-2-6-2m12 4v-3.5c0-1 .1-1.4-.5-2 2.8-.3 5.5-1.4 5.5-6a4.6 4.6 0 00-1.3-3.2 4.3 4.3 0 00-.1-3.2s-1-.3-3.4 1.3a11.6 11.6 0 00-6 0C7.3 2.3 6.3 2.6 6.3 2.6a4.3 4.3 0 00-.1 3.2A4.6 4.6 0 004.9 9c0 4.6 2.7 5.7 5.5 6-.6.6-.6 1.2-.5 2V21" />
  ),
  chevronDown: <path d="M6 9l6 6 6-6" />,
  linkedin: (
    <>
      <rect x="3" y="3" width="18" height="18" rx="2" />
      <path d="M7 10.5v6.5" />
      <path d="M7 7.5h.01" />
      <path d="M11 17v-6.5" />
      <path d="M11 13.5a2.75 2.75 0 015.5 0V17" />
    </>
  ),
  huggingface: (
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="M9 10h.01" />
      <path d="M15 10h.01" />
      <path d="M8.5 14c.9 1.2 2.1 1.8 3.5 1.8s2.6-.6 3.5-1.8" />
    </>
  ),
  trash: <path d="M4 7h16M9 7V5a1 1 0 011-1h4a1 1 0 011 1v2m-1 0v12M10 7v12M6 7l1 13a1 1 0 001 1h8a1 1 0 001-1l1-13" />,
  external: (
    <>
      <path d="M14 4h6v6" />
      <path d="M20 4l-9 9" />
      <path d="M18 14v4a2 2 0 01-2 2H6a2 2 0 01-2-2V8a2 2 0 012-2h4" />
    </>
  ),
  spark: (
    <path d="M12 3l1.6 4.6a4 4 0 002.8 2.8L21 12l-4.6 1.6a4 4 0 00-2.8 2.8L12 21l-1.6-4.6a4 4 0 00-2.8-2.8L3 12l4.6-1.6a4 4 0 002.8-2.8z" />
  ),
  menu: <path d="M4 6h16M4 12h16M4 18h16" />,
  close: <path d="M6 6l12 12M18 6L6 18" />,
  code: <path d="M8 9l-3 3 3 3M16 9l3 3-3 3M13.5 6l-3 12" />,
  shield: (
    <>
      <path d="M12 3l7 3v5c0 4.5-3 7.7-7 9-4-1.3-7-4.5-7-9V6z" />
      <path d="M9 12l2 2 4-4" />
    </>
  ),
};

export type IconName = keyof typeof PATHS;

export function Icon({
  name,
  className = "h-4 w-4",
  ...props
}: { name: IconName; className?: string } & Omit<SVGProps<SVGSVGElement>, "name">) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.75"
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
      aria-hidden="true"
      {...props}
    >
      {PATHS[name]}
    </svg>
  );
}
