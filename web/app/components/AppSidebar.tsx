"use client";

import { SidebarContent } from "./SidebarContent";

/** Desktop sidebar (≥ md). Mobile uses MobileNav (a drawer) with the same content. */
export function AppSidebar() {
  return (
    <aside className="hidden w-64 shrink-0 flex-col border-r border-slate-800/80 bg-slate-950/50 md:flex">
      <SidebarContent />
    </aside>
  );
}
