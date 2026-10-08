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
  const notice = !!request?.notice;
  return (
    <ConfirmContext.Provider value={confirm}>
      {children}
      {request && (
        <ConfirmDialog
          open
          title={request.title || (notice ? 'Done' : PROCEED)}
          description={request.description}
          confirmLabel={request.confirmLabel || (notice ? 'Done' : 'Yes, proceed')}
          cancelLabel={notice ? null : (request.cancelLabel || 'Go back')}
          tone={tone}
          icon={<Icon name={request.icon || (notice ? 'check-circle' : tone === 'danger' ? 'alert-triangle' : 'help-circle')} size={20} />}
          // Closing an end dialog any way at all is reading it.
          onClose={() => settle(notice)}
          onConfirm={() => settle(true)}
        >
          {request.details && (
            <dl style={{ margin: '12px 0 0', display: 'grid', gridTemplateColumns: 'auto 1fr', gap: '6px 14px', fontSize: 13 }}>
              {request.details.filter(([, v]) => v).map(([k, v], i) => (
                // Keyed by position as well: a list of sections can repeat a label.
                <div key={`${i}-${k}`} style={{ display: 'contents' }}>
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

/* The end dialog: what just happened, and what happens next, with one button.
 *
 *   const notice = useNotice();
 *   await notice({ title: 'Request sent', description: '…', details: [['Child', name]] });
 *
 * The owner asked for one at the end of every step of the assignment flow
 * (28 Sep 2026) - asking a psychologist, their accepting or declining, and
 * withdrawing. A toast is gone in three seconds, and "the child joins their
 * records once they accept" is the sentence somebody needs to have read. It
 * is the same dialog as useConfirm, without the way back. */
// eslint-disable-next-line react-refresh/only-export-components
export function useNotice() {
  const confirm = useContext(ConfirmContext);
  return useCallback(
    (options = {}) => (confirm ? confirm({ ...options, notice: true }) : Promise.resolve(true)),
    [confirm],
  );
}
