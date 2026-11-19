"""The three states of a document's AI summary (report or case referral).

    state      ai_summary   ai_summary_confirmed   how it gets there
    none       ""           False                  nothing drafted yet
    draft      text         False                  summarise (model output)
    confirmed  text         True                   confirm (the person's words)

Summarise on a confirmed summary is allowed: the screen asks first and the
person chose it. What is refused is a draft that was written against a summary
that has since changed, because the model call takes seconds and a confirm
or another draft can land inside them.
"""


def store_draft(doc, draft, seen_summary, seen_confirmed):
    """Save `draft` only if the summary is still what the caller read before
    asking the model. A conditional UPDATE, so a lost race stores nothing and
    returns False."""
    matched = type(doc)._default_manager.filter(
        pk=doc.pk, ai_summary=seen_summary,
        ai_summary_confirmed=seen_confirmed,
    ).update(ai_summary=draft, ai_summary_confirmed=False)
    if matched:
        doc.ai_summary = draft
        doc.ai_summary_confirmed = False
    return bool(matched)
