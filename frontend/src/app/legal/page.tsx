import Link from 'next/link';
import type { Metadata } from 'next';
import { brand } from '@/lib/brand';

export const metadata: Metadata = {
  title: 'Legal & disclosures — Alethra',
};

const SECTIONS = [
  {
    title: 'What this environment is',
    body: 'This is a research and paper-trading workspace. Market data may be sample, historical, delayed or a live feed, and each surface states its source and as-of time. Nothing here is a live brokerage account.',
  },
  {
    title: 'Execution scope',
    body: 'Orders are paper orders. They are recorded against a simulated account and are never routed to a broker or exchange from this deployment. A paper fill is not evidence that a real order could be, or was, filled.',
  },
  {
    title: 'Market data and rights',
    body: 'Provider entitlements, latency and redistribution rights depend on the account and locale in use. Intraday and delayed data shown for internal testing are not cleared for public redistribution. Coverage and holiday calendars may be incomplete, and an unknown calendar is reported as unknown rather than guessed.',
  },
  {
    title: 'Research results',
    body: 'Backtests, simulations and model evaluations describe historical or synthetic behaviour. They do not represent future performance. Small samples, selection effects and changing market conditions can make results unreliable; comparisons against a baseline are reported with the sample they were computed on.',
  },
  {
    title: 'Personal models',
    body: 'Personal models are trained on reviewed decisions in your own account. Their outputs are statistical estimates, not advice, and a signal margin or class probability is not a prediction of profit.',
  },
  {
    title: 'Not investment advice',
    body: brand.disclaimer.full,
  },
  {
    title: 'Data handling',
    body: 'Identity, consent, quotas and API access are enforced server-side. Provider credentials are stored in backend configuration only and are never returned to the browser. Usage is metered per account for the features you invoke.',
  },
];

export default function LegalPage() {
  return (
    <div className="legal-root">
      <header className="legal-bar">
        <Link href="/" className="legal-brand">
          {brand.name}
        </Link>
        <Link href="/login" className="legal-back">
          Sign in
        </Link>
      </header>
      <main className="legal-main">
        <h1 className="legal-title">Legal & disclosures</h1>
        <p className="legal-lead">
          One place for the complete limitations. Product surfaces show only the
          limitations that apply to the data or action in front of you.
        </p>
        {SECTIONS.map((section) => (
          <section key={section.title} className="legal-section">
            <h2>{section.title}</h2>
            <p>{section.body}</p>
          </section>
        ))}
      </main>
      <footer className="legal-footer">
        <span>{brand.footer.text}</span>
        <Link href="/">Back to home</Link>
      </footer>
    </div>
  );
}
