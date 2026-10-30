import type { ReactNode } from "react";
import AppShell from "@/components/shell/AppShell";

export default function ResearchDraftsLayout({ children }: { children: ReactNode }) {
  return <AppShell>{children}</AppShell>;
}
