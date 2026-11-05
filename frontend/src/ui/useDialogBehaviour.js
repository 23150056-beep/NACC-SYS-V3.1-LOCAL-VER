import { useEffect, useRef } from 'react';

/* ------------------------ Dialog behaviour (a11y) ------------------------ *
 * Everything a modal/drawer owes the keyboard: focus moves in on open, Tab is
 * trapped inside, Escape closes (when the dialog allows it), and focus returns
 * to whatever opened it. Attach the returned ref to the dialog element. */
const FOCUSABLE_SELECTOR = 'a[href],button:not([disabled]),textarea:not([disabled]),input:not([disabled]),select:not([disabled]),[tabindex]:not([tabindex="-1"])';

/* Dialogs stack — a confirm can open on top of a drawer. Only the topmost one
 * may answer Escape or trap Tab, or the layer underneath drags focus back out
 * of the dialog the user is actually looking at. */
const dialogStack = [];

export function useDialogBehaviour({ active = true, onClose, closeOnEscape = true } = {}) {
  const ref = useRef(null);
  // The handlers live in a ref so the effect's only dependency is `active`.
  // Re-running it would pull focus back to the first control — and callers
  // legitimately change these mid-dialog (a form flips `closeOnEscape` off the
  // moment it becomes dirty, i.e. on the user's first keystroke).
  const latest = useRef({ onClose, closeOnEscape });
  latest.current = { onClose, closeOnEscape };
  useEffect(() => {
    if (!active) return undefined;
    const token = {};
    dialogStack.push(token);
    const opener = document.activeElement;
    const node = ref.current;
    const visible = () => Array.from(node?.querySelectorAll(FOCUSABLE_SELECTOR) || [])
      .filter((el) => el.offsetParent !== null || el === document.activeElement);
    // Focus the first real control, else the dialog itself, so screen readers
    // land inside the dialog instead of continuing behind it.
    (visible()[0] || node)?.focus();
    const onKey = (e) => {
      if (dialogStack[dialogStack.length - 1] !== token) return;
      const { onClose: close, closeOnEscape: escapeCloses } = latest.current;
      if (e.key === 'Escape' && escapeCloses && close) {
        e.preventDefault(); e.stopPropagation(); close(); return;
      }
      if (e.key !== 'Tab' || !node) return;
      const items = visible();
      if (!items.length) return;
      const first = items[0];
      const last = items[items.length - 1];
      if (e.shiftKey && (document.activeElement === first || document.activeElement === node)) {
        e.preventDefault(); last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault(); first.focus();
      }
    };
    document.addEventListener('keydown', onKey, true);
    return () => {
      document.removeEventListener('keydown', onKey, true);
      const at = dialogStack.indexOf(token);
      if (at !== -1) dialogStack.splice(at, 1);
      if (opener instanceof HTMLElement && document.contains(opener)) opener.focus();
    };
  }, [active]);
  return ref;
}
