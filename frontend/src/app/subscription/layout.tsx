import type { ReactNode } from "react";
import AppShell from "@/components/shell/AppShell";

export default function SubscriptionLayout({ children }: { children: ReactNode }) {
  return <AppShell>{children}</AppShell>;
}
