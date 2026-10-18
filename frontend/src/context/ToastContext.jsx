import { createContext, useCallback, useContext, useMemo, useRef, useState } from 'react';
import { Alert, Icon } from '../ui';

// App-wide transaction notifications. Any component can call
//   const toast = useToast();
//   toast.success('Child record added');  // or .error(...) / .info(...)
// Toasts stack top-right, auto-dismiss after ~3.2s, and can be clicked to close.

const ToastContext = createContext(null);

const ICONS = { success: 'check-circle-2', danger: 'alert-triangle', info: 'info', warning: 'alert-circle' };
const DURATION = 3200;

export function ToastProvider({ children }) {
  const [toasts, setToasts] = useState([]);
  const idRef = useRef(0);

  const dismiss = useCallback((id) => setToasts((list) => list.filter((t) => t.id !== id)), []);

  const show = useCallback((message, tone = 'success') => {
    const id = (idRef.current += 1);
    setToasts((list) => [...list, { id, message, tone }]);
    setTimeout(() => dismiss(id), DURATION);
    return id;
  }, [dismiss]);

  const value = useMemo(() => ({
    show,
    success: (m) => show(m, 'success'),
    error: (m) => show(m, 'danger'),
    info: (m) => show(m, 'info'),
    warning: (m) => show(m, 'warning'),
  }), [show]);

  return (
    <ToastContext.Provider value={value}>
      {children}
      {/* A live region that is always in the page, so a toast added to it is
          read out. Without one the only feedback of a link to a missing
          account, or of a button that cannot act yet, was silent to a screen
          reader. Polite: a toast never needs to cut off what is being read. */}
      <div role="status" aria-live="polite" aria-atomic="false" style={{ position: 'fixed', top: 78, right: 26, zIndex: 9999, display: 'flex', flexDirection: 'column', gap: 10, pointerEvents: 'none', maxWidth: 'calc(100vw - 52px)' }}>
        {toasts.map((t) => (
          <div
            key={t.id}
            onClick={() => dismiss(t.id)}
            style={{ pointerEvents: 'auto', cursor: 'pointer', minWidth: 260, maxWidth: 380, animation: 'racco-fade-in var(--dur-base) var(--ease-out)' }}
          >
            <Alert tone={t.tone} icon={<Icon name={ICONS[t.tone] || 'info'} size={18} />} style={{ boxShadow: 'var(--shadow-lg)' }}>
              {t.message}
            </Alert>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

export function useToast() {
  const ctx = useContext(ToastContext);
  if (!ctx) throw new Error('useToast must be used within a ToastProvider');
  return ctx;
}
