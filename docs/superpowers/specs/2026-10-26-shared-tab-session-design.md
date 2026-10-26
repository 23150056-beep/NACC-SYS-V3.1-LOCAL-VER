# One sign-in for every tab in a browser

Owner's report, 26 Oct 2026: a button in a notification email opened the
sign-in form, though NACC SYS was open and signed in in the tab beside it. He
asked for that link to open the page.

## Why it happened

The tokens live in `sessionStorage` (`frontend/src/api/session.js`), which
belongs to ONE tab. That was chosen so that closing the browser ends the
session on a shared office computer, and its cost was written down at the
time: "opening the app in a second tab means signing in there too". A link
from Gmail always opens a new tab, so every email button met the form.
Being signed in to Chrome or Gmail with a Google account has nothing to do
with it: that is Google's session, not ours.

## What changes

A tab that opens with no session **borrows** one from a tab of this site that
is already signed in, in the same browser. Nothing else about where the
session lives changes: still `sessionStorage`, never `localStorage`, so
closing every tab still ends it.

### Whose session a tab holds

**The token says.** Every SimpleJWT token carries `user_id`. The browser
reads it out of the refresh token's payload (`heldUserId()`). It does not
verify it, because the server does on every use. That id is the "user" in
every message and every check below. It is also known while a tab's own
user is still loading. And a tab handing on its tokens cannot label them
with anybody else's id, which a label taken from the screen's state could do
for a frame after a sign-in.

### Asking

- On start, a tab with no token posts `ask` on a `BroadcastChannel` named
  `nacc-session` and waits at most 400 ms. A signed-in tab answers `share`
  with its access and refresh tokens and its user id. The asker stores the
  tokens in its own `sessionStorage` and carries on as if it had signed in:
  `/auth/me/` as usual.
- **Who answers.** Only a tab whose user has loaded, that holds both tokens,
  whose user is not in the middle of a forced password change, and whose
  session has not been signed out (below).
- **An email link names whose it is.** Every notification email's button
  carries `for=<the recipient's user id>` (`accounts/email_notifications.py`
  `render`), and so do the "Choose which emails you get" address in every
  email's footer and the link in a session's calendar invite.
  - A tab opened with `for` takes the first share from THAT user and no
    other. With none in 400 ms it shows the form.
  - Two people signed in in one browser each get their own session from their
    own email. A psychologist clicking their email on a PC where a social
    worker is signed in gets the form, not the social worker's account.
  - `/auth/me/` must then name that user, or the tokens are dropped.
  - `for` is compared as a number (`01` is `1`).
  - It is **taken off the address as the app starts**, before the router reads
    it. A bookmark or a copied address therefore never carries it, and a
    colleague's tab is not restricted by somebody else's link.
  - It is not a secret and signs nobody in. It only says whose open session
    to look for. Somebody at a browser where that person is signed in could
    already click their tab.
- **Without `for`** (a bookmark, a typed address, the plain address printed
  under an email's button if a mail client turns it into a link): after the
  first share the asker waits 80 ms more, and borrows only if every share
  names the same user. Different users means the form. A second account's tab
  that is frozen, discarded or busy past the 80 ms is not heard. That case is
  accepted, because it is the same as clicking the open tab.
- **Where it never borrows.**
  - `/login`, `/signup` and `/survey/<token>`, matched on the decoded,
    lower-cased path (the router matches routes either way; a path that cannot
    be decoded does not borrow).
  - `/login` must always reach the form. A person who goes there means to
    sign in as themselves, perhaps as somebody other than the account open in
    the next tab. A tab whose session died is sent there too, and must not
    come up as whoever else is signed in.
  - A child fills in the survey with no account, and a staff token must not
    ride along on its requests. Signup is an applicant's page.
  - **The temporary-password email's button goes to `/login`**
    (`children/notifications.py`). The reset or approval behind it ended every
    session the person had, so any session this browser holds is somebody
    else's.
  - **A tab that has just dropped a signed-out session** at start-up does not
    borrow either, for the same reason as a dead one. The drop is decided once,
    as the module loads. React's development mode runs start-up effects twice,
    and a decision made inside the effect was forgotten by the second run,
    which then borrowed.
  - A path the router does not know is sent to `/login` before start-up reads
    the address, so it does not borrow either.

### Signing out

- **Signing out signs out every tab of that account in this browser.** The
  tab that signs out posts `signed-out` with its user id and the last 24
  characters (the tail) of its refresh token.
  - A tab drops its session when its tail matches, or when the session it
    holds is that user's. Both work while the tab's user is still loading,
    because the id comes from the token.
  - A tab signed in as somebody else is left alone.
- **The tab that signs out** runs the full clean-up, as before: tokens, its
  Recent list, and every unsaved draft in `localStorage`. The draft sweep has
  always removed every draft key, not only the person's own. In a browser two
  people are signed in to, that includes the other person's unsaved
  backups. Known, and left as it was.
- **A tab told by another** clears only its tokens, its own Recent list and
  its user, and stops waiting if it was still loading. It does not touch the
  drafts: the tab that signed out already did. A message that arrives late,
  to a tab that was frozen, would otherwise delete drafts typed since by
  whoever signed in next.
- Changing a password already calls `logout()`, so it reaches every tab,
  which is right: every token from before is dead.
- **A tab that was not running when it happened is caught too.** Examples are
  a tab discarded by Memory Saver, and one navigated to another site and come
  back with Back. The sign-out also writes `{tail, userId, at}` to
  `localStorage` (`nacc-signed-out`, each entry kept 24 hours, the refresh
  token's lifetime, pruned whenever the list is read or written). A tail and
  an id are not credentials.
  - At start-up, and when a page is restored from the back/forward cache
    (`pageshow`), a tab drops what it holds if its tail is listed, or if its
    user is listed. No clocks are compared.
  - A share whose tail or user is listed is refused.
- **Signing in again lifts the account's entry, not the old sessions'.** A
  sign-in through the form or Google blanks that user's id in their entries,
  so the person's new session is not taken for the old one. The tails stay
  listed, so a tab still holding a session from before the sign-out stays
  dead after it.
- A tab also checks the list when it becomes visible again
  (`visibilitychange`), in case a frozen tab missed the message.
- **What arrives late is not stored, and refusals hit only their own
  session.**
  - A borrow that hears `signed-out` or `ended` for a share it holds discards
    that share.
  - `/auth/me/`'s answer, and a refresh's, are applied only if the tab still
    holds the session they were sent with.
  - A refused refresh, or a "password was changed" refusal, ends a session
    only if it is still the one the request was sent with (the tail is
    stamped on the request). Otherwise a reply about the old session would
    end the new one.
- **Open confirmation and notice dialogs close** when the tab's user signs
  out: a confirmation answers no, and a notice closes as when dismissed. A
  "Yes" over the sign-in page would send a save with no session.
- **A request answered after its session ended is not retried** under the
  session the tab holds now. A 401 for the old session is refreshed only if
  the session it was sent with is still the one held. Otherwise A's save,
  re-sent after B signed in in that tab, would land in B's account.
- **The sign-out question says so**: "This signs you out of every tab in this
  browser that is signed in to your account. What is saved is kept; typing
  not yet saved in those tabs is lost."

### A session that has died

- **"Died" means the server refused it**: the refresh answered 401, or the
  "password was changed" refusal. That tab posts `ended` with the tail, and
  any tab holding the same refresh token drops it.
- Any other refresh failure (a network drop, a 5xx while the demo wakes)
  ends only that tab's session, as before, and tells nobody. Otherwise one
  hiccup would sign out every tab of a session that is still good.
- The dead tab goes to `/login`, which does not borrow. It cannot pick the
  dead session back up from a tab that has not noticed yet, nor come up as
  somebody else.

### No `BroadcastChannel`

In very old browsers and some locked-down contexts there is no borrowing,
exactly as before. Blocked site data makes `sessionStorage` throw; start-up
always clears its loading state, so the screen never hangs on "Connecting".

## Why this is safe, and what it costs

- The channel is **same-origin**: only pages of this site, in this browser
  profile, can ask. Another site cannot, nor can incognito or another Chrome
  profile.
- **The email carries nothing that signs anyone in.** A forwarded email,
  another computer or another browser still gets the form, and the emails'
  safety line ("nothing in this email signs you in") stays true.
- **Script injected into ANY page of the site can now obtain the tokens of
  every signed-in tab** by asking, a tokenless `/survey/` or `/signup` page
  included. Before, such a page held nothing. This rests on the rule that
  nothing can run script on this origin: React escapes what it renders, and
  downloaded files are typed as PDF or Word and never render as a page. Any
  change that loosens that rule must consider this.
- Tokens are not rotated on refresh (`SIMPLE_JWT` sets no
  `ROTATE_REFRESH_TOKENS`), so tabs sharing one refresh token cannot sign each
  other out by refreshing. **If rotation is ever turned on, this must be
  revisited.** One tab's refresh would blacklist the token the others hold.
- The token's `user_id` claim is read in the browser. If `USER_ID_CLAIM` is
  ever changed, `heldUserId()` must follow it.
- **Signing out is still only the browser forgetting.** The server never
  revokes a token: there is no blacklist, before or after this change. The
  signed-out list makes this browser keep forgetting, and no more. A server
  logout that blacklists the refresh token is the complete answer, and a
  separate piece of work.
- What is new: while somebody is signed in with a tab open, a person at their
  desk can open another tab, or a bookmark, and be in. They could already
  click the open tab.

## Known and left

Each of these was found in review and judged not worth its code here:
- **The sign-in page keeps the address a tab was on**, and goes there after
  the next sign-in. After a sign-out from another tab, the next person to
  sign in there lands on the page the previous one was reading. The server
  still decides what they may see. The same happened before when a session
  expired.
- **A reply already on its way** to the bell's feed or the dashboard figures
  when a sign-out lands can still fill them for a moment, until the next
  sign-in fetches its own. Before this change it could happen only in one tab.
- **The draft sweep on sign-out** removes every draft key, the other signed-in
  person's included (above).
- **A second, separately signed-in session of the same account comes back
  after the person signs in again.** It was never borrowed, so its tail was
  never listed, and the account's entry is what the new sign-in lifts.
  Example: a tab discarded while signed in on its own. The person is at the
  browser again by then; the session that was actually signed out stays dead.

## What it does not help

The first click of the day with no NACC SYS tab open still meets the form.
So do Gmail's own in-app browser on a phone, and a different Chrome profile
or incognito window. A tab that Chrome's Memory Saver has discarded cannot
answer. A tab still loading its own user does not answer either, so a link
clicked during a slow start meets the form.
