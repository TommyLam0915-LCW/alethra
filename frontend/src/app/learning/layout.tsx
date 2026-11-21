import type { ReactNode } from "react";
import AppShell from "@/components/shell/AppShell";

export default function LearningLayout({ children }: { children: ReactNode }) {
  return <AppShell active="training">{children}</AppShell>;
}
