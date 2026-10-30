import type { ReactNode } from "react";
import AppShell from "@/components/shell/AppShell";

export default function ModelsLayout({ children }: { children: ReactNode }) {
  return <AppShell active="models">{children}</AppShell>;
}
