import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';
import { Alert, Icon, iconBtn } from '../ui';

// App-wide transaction notifications. Any component can call
//   const toast = useToast();
//   toast.success('Child record added');  // or .error(...) / .info(...)
// Toasts stack top-right and can be closed with their button (or, for a
// success or an info, a click anywhere on them).
//
// A toast is read out as well as seen. The container holds two live regions
// that are on the page from the first render, so a screen reader is already
// listening when a message lands in one: errors in `role="alert"`, which
// interrupts, everything else in `role="status"`, which waits its turn. A
// region that appears together with its text is often not announced at all.
//
// Every toast goes by itself (3 Nov 2026, owner's request: an error sat on
// the sign-in page until it was clicked). A success or an info leaves after a
// time that grows with the length of the sentence, 5 to 15 seconds. An error
// stays longer, 8 to 20 seconds, because the person has to read it and often
// find what failed; it can go earlier, since what it reports is also said where
// it happened (the sign-in form's own alert, a field's message). The time stops
// while the pointer is on the toast or the keyboard focus is inside it, and
// while the page is hidden, so a message raised while the person was on another
// browser tab is still there when they come back; it then starts over in full.
// The X button and Escape (the newest error) close a toast at once.
//
// The same message, in the same tone, is never stacked: a toast already on
// screen is replaced by a fresh one with the same id, so its time starts over
// and the live region reads it again. A message that is not a string (a React
// node) is never compared. show() returns the id, and dismiss(id) closes it.

const ToastContext = createContext(null);

const ICONS = { success: 'check-circle-2', danger: 'alert-triangle', info: 'info', warning: 'alert-circle' };

const MIN_MS = 5000;
const MAX_MS = 15000;
// Roughly a slow reader: a second to notice it, then about 14 characters a second.
const toastDuration = (message) => {
  const length = typeof message === 'string' ? message.length : 60;
  return Math.min(MAX_MS, Math.max(MIN_MS, 1000 + length * 70));
};

const ERROR_MIN_MS = 8000;
const ERROR_MAX_MS = 20000;
const errorDuration = (message) => {
  const length = typeof message === 'string' ? message.length : 60;
  return Math.min(ERROR_MAX_MS, Math.max(ERROR_MIN_MS, 2000 + length * 90));
};

function usePageHidden() {
  const [hidden, setHidden] = useState(() => typeof document !== 'undefined' && document.visibilityState === 'hidden');
  useEffect(() => {
    const onChange = () => setHidden(document.visibilityState === 'hidden');
    document.addEventListener('visibilitychange', onChange);
    return () => document.removeEventListener('visibilitychange', onChange);
  }, []);
  return hidden;
}

function ToastItem({ toast, onDismiss }) {
  const { id, message, tone } = toast;
  const isError = tone === 'danger';
  const [held, setHeld] = useState(false);
  const hidden = usePageHidden();
  useEffect(() => {
    if (held || hidden) return undefined;
    const timer = setTimeout(() => onDismiss(id), toast.duration);
    return () => clearTimeout(timer);
  }, [held, hidden, id, toast.duration, onDismiss]);

  return (
    <div
      onMouseEnter={() => setHeld(true)}
      onMouseLeave={() => setHeld(false)}
      onFocus={() => setHeld(true)}
      onBlur={() => setHeld(false)}
      // An error is read, and often copied; only its own button closes it.
      onClick={isError ? undefined : () => onDismiss(id)}
      style={{ pointerEvents: 'auto', cursor: isError ? 'default' : 'pointer', minWidth: 260, maxWidth: 380, marginBottom: 10, position: 'relative', animation: 'racco-fade-in var(--dur-base) var(--ease-out)' }}
    >
      <Alert tone={tone} icon={<Icon name={ICONS[tone] || 'info'} size={18} aria-hidden="true" />} style={{ boxShadow: 'var(--shadow-lg)', paddingRight: 44 }}>
        {message}
      </Alert>
      <button
        type="button"
        aria-label={isError ? 'Dismiss error message' : 'Dismiss message'}
        onClick={(e) => { e.stopPropagation(); onDismiss(id); }}
        style={{ ...iconBtn('var(--text-muted)', 26), position: 'absolute', top: 8, right: 8 }}
      >
        <Icon name="x" size={14} aria-hidden="true" />
      </button>
    </div>
  );
}

export function ToastProvider({ children }) {
  const [toasts, setToasts] = useState([]);
  const idRef = useRef(0);

  // The list is mirrored in a ref so show() can look for a repeat without
  // doing it inside a state updater (which React may run twice).
  const listRef = useRef([]);
  const commit = useCallback((next) => { listRef.current = next; setToasts(next); }, []);

  const dismiss = useCallback((id) => commit(listRef.current.filter((t) => t.id !== id)), [commit]);

  const show = useCallback((message, tone = 'success') => {
    const duration = tone === 'danger' ? errorDuration(message) : toastDuration(message);
    const same = typeof message === 'string'
      ? listRef.current.find((t) => t.tone === tone && t.message === message)
      : undefined;
    if (same) {
      // Same id, new version: the node is replaced, so the time and the
      // entrance start over and the live region announces it again.
      const fresh = { ...same, version: same.version + 1, duration };
      commit([...listRef.current.filter((t) => t.id !== same.id), fresh]);
      return same.id;
    }
    const id = (idRef.current += 1);
    commit([...listRef.current, { id, version: 0, message, tone, duration }]);
    return id;
  }, [commit]);

  const value = useMemo(() => ({
    show,
    dismiss,
    success: (m) => show(m, 'success'),
    error: (m) => show(m, 'danger'),
    info: (m) => show(m, 'info'),
    warning: (m) => show(m, 'warning'),
  }), [show, dismiss]);

  // Escape closes the newest error when nothing else wanted the key: a dialog
  // that closes on Escape has already handled it (and stopped it) by then, so
  // this never shuts an error behind a dialog's back. It lets a person clear an
  // error at once without reaching for the mouse or tabbing to its button.
  const errorsRef = useRef([]);
  errorsRef.current = toasts.filter((t) => t.tone === 'danger');
  useEffect(() => {
    const onKey = (e) => {
      if (e.key !== 'Escape' || e.defaultPrevented) return;
      const newest = errorsRef.current[errorsRef.current.length - 1];
      if (newest) dismiss(newest.id);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [dismiss]);

  const errors = toasts.filter((t) => t.tone === 'danger');
  const others = toasts.filter((t) => t.tone !== 'danger');

  return (
    <ToastContext.Provider value={value}>
      {children}
      <div style={{ position: 'fixed', top: 78, right: 26, zIndex: 9999, display: 'flex', flexDirection: 'column', pointerEvents: 'none', maxWidth: 'calc(100vw - 52px)' }}>
        {/* Both regions are always mounted, empty or not. `aria-atomic` is off so
            a second message is read on its own rather than after the first again. */}
        <div role="alert" aria-atomic="false">
          {errors.map((t) => <ToastItem key={`${t.id}:${t.version}`} toast={t} onDismiss={dismiss} />)}
        </div>
        <div role="status" aria-live="polite" aria-atomic="false">
          {others.map((t) => <ToastItem key={`${t.id}:${t.version}`} toast={t} onDismiss={dismiss} />)}
        </div>
      </div>
    </ToastContext.Provider>
  );
}

export function useToast() {
  const ctx = useContext(ToastContext);
  if (!ctx) throw new Error('useToast must be used within a ToastProvider');
  return ctx;
}
