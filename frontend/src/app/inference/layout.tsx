import type { ReactNode } from "react";
import AppShell from "@/components/shell/AppShell";

export default function InferenceLayout({ children }: { children: ReactNode }) {
  return <AppShell active="training">{children}</AppShell>;
}
