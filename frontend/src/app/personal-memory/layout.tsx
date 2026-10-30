import type { ReactNode } from "react";
import AppShell from "@/components/shell/AppShell";

export default function PersonalMemoryLayout({ children }: { children: ReactNode }) {
  return <AppShell active="training">{children}</AppShell>;
}
