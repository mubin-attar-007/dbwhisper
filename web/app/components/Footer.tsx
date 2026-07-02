import { Icon, type IconName } from "./Icon";
import { LINKS } from "./links";

const FOOTER_LINKS: { label: string; icon: IconName; href: string }[] = [
  { label: "GitHub", icon: "github", href: LINKS.github },
  { label: "LinkedIn", icon: "linkedin", href: LINKS.linkedin },
  { label: "Hugging Face", icon: "huggingface", href: LINKS.huggingface },
  { label: "API docs", icon: "book", href: LINKS.apiDocs },
];

export function Footer() {
  return (
    <footer className="border-t border-slate-800/60">
      <div className="mx-auto flex max-w-4xl flex-wrap items-center gap-x-4 gap-y-2 px-4 py-6 text-xs text-slate-400">
        {FOOTER_LINKS.map((item) => (
          <a
            key={item.label}
            href={item.href}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-1.5 rounded transition hover:text-slate-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-400 focus-visible:ring-offset-2 focus-visible:ring-offset-slate-950"
          >
            <Icon name={item.icon} className="h-4 w-4" />
            {item.label}
          </a>
        ))}
        <span className="text-slate-500">Powered by DBWhisper — natural language → SQL</span>
      </div>
    </footer>
  );
}
