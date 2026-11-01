import type { ReactNode } from 'react';
import AppShell from '@/components/shell/AppShell';

export default function StrategyTemplatesLayout({ children }: { children: ReactNode }) {
  return <AppShell active="strategies">{children}</AppShell>;
}
