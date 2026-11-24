import { Suspense } from 'react';
import JournalDashboard from '@/components/journal/JournalDashboard';

export const metadata = { title: 'Unified Journal | Alethra' };

// The unified ledger view has an address of its own, so a link can be sent to
// it, bookmarked, and reloaded without the client bundle first opening Trades
// and then jumping. `initialTab` seeds the panel; the dashboard keeps one tab
// bar for every view, so this page and `/journal` cannot drift into two
// different navigations.
//
// `useSearchParams` on a statically rendered route has to sit behind a Suspense
// boundary, or the route cannot be prerendered at all.
export default function UnifiedJournalPage() {
  return (
    <Suspense
      fallback={
        <div style={{ padding: 32, color: 'var(--text-secondary)', fontSize: 14 }}>
          Loading unified journal…
        </div>
      }
    >
      <JournalDashboard initialTab="unified" />
    </Suspense>
  );
}
