"""The survey link's status table: the only place its legal moves are written.

A link is answered once. EXPIRED is a state the table knows but nothing ever
writes: whether a link has expired is decided by the clock (`is_open`, and
`expires_at` in the submit write), and care gaps and brief facts read it that
way. Do not add a writer; a stored EXPIRED would only be a second copy of the
clock that can disagree with it.
"""
from clinical.models import OpinionnaireInvite
from config.lifecycle import Lifecycle

# One rule: a link lives this long, and a social worker is reminded when one
# has gone unanswered this long (clinical/care_gaps.py).
INVITE_DAYS = 7

INVITE_LIFECYCLE = Lifecycle(OpinionnaireInvite, "status", {
    OpinionnaireInvite.PENDING: {OpinionnaireInvite.SUBMITTED, OpinionnaireInvite.EXPIRED},
    OpinionnaireInvite.SUBMITTED: set(),
    OpinionnaireInvite.EXPIRED: set(),
}, name="survey link")
