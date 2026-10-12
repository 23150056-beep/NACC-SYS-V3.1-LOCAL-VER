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
// An error stays until it is dismissed: the thing it reports is something the
// person has to act on, and three seconds is not long enough to read it, let
// alone find what failed. A success or an info goes by itself, after a time
// that grows with the length of the sentence (5 seconds at the least) and
// that stops while the pointer or the keyboard is on it.

const ToastContext = createContext(null);

const ICONS = { success: 'check-circle-2', danger: 'alert-triangle', info: 'info', warning: 'alert-circle' };

const MIN_MS = 5000;
const MAX_MS = 15000;
// Roughly a slow reader: a second to notice it, then about 14 characters a second.
const toastDuration = (message) => {
  const length = typeof message === 'string' ? message.length : 60;
  return Math.min(MAX_MS, Math.max(MIN_MS, 1000 + length * 70));
};

function ToastItem({ toast, onDismiss }) {
  const { id, message, tone } = toast;
  const sticky = tone === 'danger';
  const [held, setHeld] = useState(false);
  useEffect(() => {
    if (sticky || held) return undefined;
    const timer = setTimeout(() => onDismiss(id), toast.duration);
    return () => clearTimeout(timer);
  }, [sticky, held, id, toast.duration, onDismiss]);

  return (
    <div
      onMouseEnter={() => setHeld(true)}
      onMouseLeave={() => setHeld(false)}
      onFocus={() => setHeld(true)}
      onBlur={() => setHeld(false)}
      // An error is read, and often copied; only its own button closes it.
      onClick={sticky ? undefined : () => onDismiss(id)}
      style={{ pointerEvents: 'auto', cursor: sticky ? 'default' : 'pointer', minWidth: 260, maxWidth: 380, marginBottom: 10, position: 'relative', animation: 'racco-fade-in var(--dur-base) var(--ease-out)' }}
    >
      <Alert tone={tone} icon={<Icon name={ICONS[tone] || 'info'} size={18} aria-hidden="true" />} style={{ boxShadow: 'var(--shadow-lg)', paddingRight: 44 }}>
        {message}
      </Alert>
      <button
        type="button"
        aria-label={sticky ? 'Dismiss error message' : 'Dismiss message'}
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

  const dismiss = useCallback((id) => setToasts((list) => list.filter((t) => t.id !== id)), []);

  const show = useCallback((message, tone = 'success') => {
    const id = (idRef.current += 1);
    setToasts((list) => [...list, { id, message, tone, duration: toastDuration(message) }]);
    return id;
  }, []);

  const value = useMemo(() => ({
    show,
    success: (m) => show(m, 'success'),
    error: (m) => show(m, 'danger'),
    info: (m) => show(m, 'info'),
    warning: (m) => show(m, 'warning'),
  }), [show]);

  // Escape closes the newest error when nothing else wanted the key: a dialog
  // that closes on Escape has already handled it (and stopped it) by then, so
  // this never shuts an error behind a dialog's back. Without it an error that
  // stays on screen could only be reached by tabbing through the whole page.
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
          {errors.map((t) => <ToastItem key={t.id} toast={t} onDismiss={dismiss} />)}
        </div>
        <div role="status" aria-live="polite" aria-atomic="false">
          {others.map((t) => <ToastItem key={t.id} toast={t} onDismiss={dismiss} />)}
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
