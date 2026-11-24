import { Suspense } from 'react';
import JournalDashboard from '@/components/journal/JournalDashboard';

// `JournalDashboard` reads `?tab=` to decide which panel opens, and reading the
// search string from a statically rendered page has to be behind a Suspense
// boundary — without it the route cannot be prerendered at all. The fallback is
// the shape the panel arrives in, so the wait costs a blank card and not a
// layout jump.
export default function JournalPage() {
  return (
    <Suspense
      fallback={
        <div style={{ padding: 32, color: 'var(--text-secondary)', fontSize: 14 }}>
          Loading trading journal…
        </div>
      }
    >
      <JournalDashboard />
    </Suspense>
  );
}
