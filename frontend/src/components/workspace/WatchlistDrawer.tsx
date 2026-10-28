'use client';

import { useEffect, useRef, type ReactNode } from 'react';

const FOCUSABLE = 'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])';

/**
 * Off-canvas-quality behaviour for the inline watchlist drawer: it owns the
 * accessible name, moves focus in on open, traps Tab so the keyboard cannot
 * reach the obscured workspace behind it, and hands focus back to the trigger
 * on close.
 */
export default function WatchlistDrawer({
  onClose,
  returnFocusRef,
  children,
}: {
  onClose: () => void;
  returnFocusRef: { current: HTMLElement | null };
  children: ReactNode;
}) {
  const boxRef = useRef<HTMLDivElement>(null);
  const closeRef = useRef(onClose);
  closeRef.current = onClose;

  useEffect(() => {
    const box = boxRef.current;
    box?.focus();

    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.preventDefault();
        closeRef.current();
        return;
      }
      if (e.key !== 'Tab' || !box) return;
      const focusable = Array.from(box.querySelectorAll<HTMLElement>(FOCUSABLE)).filter(
        (el) => !el.hasAttribute('disabled') && el.offsetParent !== null
      );
      if (focusable.length === 0) {
        e.preventDefault();
        return;
      }
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      const active = document.activeElement as HTMLElement | null;
      const inside = !!active && box.contains(active);
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
      id="watchlist-drawer"
      className="ws-watchlist-drawer"
      role="dialog"
      aria-modal="true"
      aria-label="觀察列表"
      tabIndex={-1}
      data-testid="watchlist-drawer"
    >
      {children}
    </div>
  );
}
