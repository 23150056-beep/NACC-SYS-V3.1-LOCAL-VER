import { useEffect } from 'react';
import { Link } from 'react-router-dom';

/* The shell both Login and Signup sit in.
 *
 * Extracted rather than duplicated: the two pages are reached one after the
 * other, so any drift between them — a different card width, a heavier
 * shadow, the seal a few pixels off — reads as the second page belonging to a
 * different system. Sharing the frame means only the form differs, which is
 * the only thing that should.
 *
 * Sizing is deliberately NOT inline. Everything vertical scales with the
 * viewport height and the card is capped at the window (see .racco-auth-* in
 * index.css), because the request-access form is tall enough to run off the
 * bottom of a 768p laptop at 100% zoom. Hard-coded paddings here would fight
 * that and win.
 */

/* One link style for both pages. Two hand-written inline styles drifted by a
 * font weight the first time this was written; this is the fix. */
export function AuthLink({ to, children }) {
  return (
    <Link to={to} style={{ color: 'var(--blue-600)', fontWeight: 700, textDecoration: 'none' }}>
      {children}
    </Link>
  );
}

export default function AuthLayout({ heading, subheading, children, footer = null, title = null }) {
  // These are the only two screens reached before the app shell mounts, so
  // nothing else is setting the tab title — without this both read whatever
  // index.html says, and a browser with several tabs open shows two identical
  // ones.
  useEffect(() => {
    if (!title) return undefined;
    const previous = document.title;
    document.title = `${title} · NACC RACCO I`;
    return () => { document.title = previous; };
  }, [title]);

  return (
    <div className="racco-sky-wash racco-auth-wash">
      <div className="racco-login-card"
           style={{ background: 'var(--surface)', borderRadius: 'var(--radius-xl)',
                    boxShadow: 'var(--shadow-xl)', overflow: 'hidden' }}>

        {/* Brand panel. Flat chrome navy, not a gradient: this is the same
            surface the signed-in header uses, so signing in reads as walking
            into the building rather than arriving somewhere else. */}
        <div className="racco-login-brand" style={{ background: 'var(--chrome)', color: '#fff' }}>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
            <img src="/racco-seal.jpg" alt="NACC seal" className="racco-auth-seal"
                 style={{ borderRadius: '50%', objectFit: 'cover',
                          boxShadow: '0 6px 18px rgba(0,0,0,0.28)', flex: 'none' }} />
            <div style={{ minWidth: 0 }}>
              <div className="racco-auth-org"
                   style={{ fontFamily: 'var(--font-display)', fontWeight: 800, lineHeight: 1.2 }}>
                NACC &ndash; RACCO 1
              </div>
              <div style={{ fontFamily: 'var(--font-sans)', fontWeight: 700, fontSize: 11, letterSpacing: '0.1em', textTransform: 'uppercase', color: 'var(--blue-300)', marginTop: 6 }}>
                Regional Alternative Child Care Office I
              </div>
            </div>
            <p className="racco-login-tagline" style={{ fontSize: 14, lineHeight: 1.6, color: 'var(--blue-200)', maxWidth: 300 }}>
              Child case management and counseling support for the children in RACCO I&rsquo;s care.
            </p>
          </div>
          {/* The first thing to go on a short window — it is the one part of
              the page carrying no information the user needs to act on. */}
          <div className="racco-login-tagline"
               style={{ fontWeight: 600, fontSize: 13, lineHeight: 1.5, color: '#fff', borderLeft: '3px solid var(--amber-400)', paddingLeft: 14 }}>
            Every child in our care is known by name, not by number.
          </div>
        </div>

        {/* Form panel. Scrolls inside itself on a window too short for the
            form, so the submit button is always reachable. */}
        <div className="racco-auth-panel racco-scroll">
          <h1 style={{ fontFamily: 'var(--font-display)', fontWeight: 800, color: 'var(--text-strong)' }}>
            {heading}
          </h1>
          {subheading && (
            <p style={{ fontSize: 13, color: 'var(--text-muted)', marginTop: 4, lineHeight: 1.5 }}>
              {subheading}
            </p>
          )}
          {children}
          {footer && (
            <div style={{ marginTop: 'clamp(10px, 1.8vh, 20px)',
                          paddingTop: 'clamp(9px, 1.5vh, 16px)',
                          borderTop: '1px solid var(--border)',
                          fontSize: 13, color: 'var(--text-muted)', textAlign: 'center' }}>
              {footer}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
