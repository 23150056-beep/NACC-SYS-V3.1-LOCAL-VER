import { createContext, useCallback, useContext, useRef, useState } from 'react';
import { ConfirmDialog, Icon } from '../ui';

/* "Are you sure you want to proceed?" before anything is saved, sent or
 * changed — asked the owner's way, on every screen, from one place.
 *
 *   const confirm = useConfirm();
 *   if (!(await confirm({ description: 'This adds the record to Records.' }))) return;
 *
 * One dialog for the whole app, rather than a ConfirmDialog and an `open`
 * flag written into every form: forty copies of the same four lines drift,
 * and the one that drifts is the one that saves without asking.
 *
 * It resolves false on Cancel, Escape or a click outside, so the caller's
 * only job is to stop. Destructive actions keep their own dialogs — those
 * ask for a reason or a typed name, which is more than a yes.
 */
const ConfirmContext = createContext(null);

export const PROCEED = 'Are you sure you want to proceed?';

export function ConfirmProvider({ children }) {
  const [request, setRequest] = useState(null);
  // Held in a ref as well, so a second confirm() arriving before the first is
  // answered settles the first as "no" instead of leaving its caller waiting.
  const pending = useRef(null);

  const confirm = useCallback((options = {}) => new Promise((resolve) => {
    pending.current?.(false);
    pending.current = resolve;
    setRequest(options);
  }), []);

  const settle = (answer) => {
    const resolve = pending.current;
    pending.current = null;
    setRequest(null);
    resolve?.(answer);
  };

  const tone = request?.tone || 'brand';
  return (
    <ConfirmContext.Provider value={confirm}>
      {children}
      {request && (
        <ConfirmDialog
          open
          title={request.title || PROCEED}
          description={request.description}
          confirmLabel={request.confirmLabel || 'Yes, proceed'}
          cancelLabel={request.cancelLabel || 'Go back'}
          tone={tone}
          icon={<Icon name={request.icon || (tone === 'danger' ? 'alert-triangle' : 'help-circle')} size={20} />}
          onClose={() => settle(false)}
          onConfirm={() => settle(true)}
        >
          {request.details && (
            <dl style={{ margin: '12px 0 0', display: 'grid', gridTemplateColumns: 'auto 1fr', gap: '6px 14px', fontSize: 13 }}>
              {request.details.filter(([, v]) => v).map(([k, v]) => (
                <div key={k} style={{ display: 'contents' }}>
                  <dt style={{ color: 'var(--text-muted)', fontWeight: 600 }}>{k}</dt>
                  <dd style={{ margin: 0, color: 'var(--text-strong)', fontWeight: 700, minWidth: 0, overflowWrap: 'anywhere' }}>{v}</dd>
                </div>
              ))}
            </dl>
          )}
        </ConfirmDialog>
      )}
    </ConfirmContext.Provider>
  );
}

/* Outside the provider there is nobody to ask, and a save that silently never
 * happens is worse than one that happens unasked — so it proceeds. Every
 * signed-in screen and the sign-up page sit inside the provider (App.jsx). */
const proceed = () => Promise.resolve(true);

// eslint-disable-next-line react-refresh/only-export-components
export function useConfirm() {
  return useContext(ConfirmContext) || proceed;
}
