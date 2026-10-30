import type { ReactNode } from "react";
import AppShell from "@/components/shell/AppShell";

export default function ExecutionLayout({ children }: { children: ReactNode }) {
  return <AppShell active="portfolios">{children}</AppShell>;
}
