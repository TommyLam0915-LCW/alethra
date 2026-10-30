import type { ReactNode } from "react";
import AppShell from "@/components/shell/AppShell";

export default function ExperimentsLayout({ children }: { children: ReactNode }) {
  return <AppShell active="strategies">{children}</AppShell>;
}
