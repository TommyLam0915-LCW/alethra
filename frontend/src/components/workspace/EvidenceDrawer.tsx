'use client';

import { useEffect, useRef, type ReactNode } from 'react';

const FOCUSABLE = 'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])';

/**
 * The evidence detail drawer: opens beside the dock, keeps the workspace behind it
 * exactly where it was, and never navigates. Focus moves in on open, Tab is trapped
 * so the keyboard cannot reach the obscured tables, Escape closes, and focus returns
 * to the row that opened it.
 */
export default function EvidenceDrawer({
  testId,
  ariaLabel,
  onClose,
  returnFocusRef,
  children,
}: {
  testId: string;
  ariaLabel: string;
  onClose: () => void;
  returnFocusRef: { current: HTMLElement | null };
  children: ReactNode;
}) {
  const boxRef = useRef<HTMLDivElement>(null);
  const closeRef = useRef(onClose);
  closeRef.current = onClose;

  useEffect(() => {
    boxRef.current?.focus();

    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.preventDefault();
        closeRef.current();
        return;
      }
      if (e.key !== 'Tab' || !boxRef.current) return;
      const focusable = Array.from(boxRef.current.querySelectorAll<HTMLElement>(FOCUSABLE)).filter(
        (el) => !el.hasAttribute('disabled') && el.offsetParent !== null
      );
      if (focusable.length === 0) {
        e.preventDefault();
        return;
      }
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      const active = document.activeElement as HTMLElement | null;
      const inside = !!active && boxRef.current.contains(active);
      if (e.shiftKey && (!inside || active === first)) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && (!inside || active === last)) {
        e.preventDefault();
        first.focus();
      }
    };

    document.addEventListener('keydown', onKeyDown);
    return () => {
      document.removeEventListener('keydown', onKeyDown);
      const trigger = returnFocusRef.current;
      if (trigger && trigger.offsetParent !== null) trigger.focus();
    };
  }, [returnFocusRef]);

  return (
    <div
      ref={boxRef}
      className="ws-evidence-drawer"
      role="dialog"
      aria-modal="true"
      aria-label={ariaLabel}
      tabIndex={-1}
      data-testid={testId}
    >
      <div className="ws-evidence-drawer-bar">
        <button
          type="button"
          className="ws-btn ws-btn-neutral"
          onClick={onClose}
          data-testid={`${testId}-close`}
        >
          Close details
        </button>
      </div>
      {children}
    </div>
  );
}
