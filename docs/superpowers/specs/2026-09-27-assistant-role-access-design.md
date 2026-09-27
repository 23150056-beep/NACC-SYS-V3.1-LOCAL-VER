# The Assistant, Role by Role — Audit and Next Steps

**Date:** 2026-09-27
**Status:** Audit done and fixes shipped on this branch; next steps are proposals for the owner
**Builds on:** `2026-08-25-local-ai-assistant-design.md`, `2026-08-26-assistant-chatbot-design.md`

## The question

Can the assistant show a child's sensitive data to someone who should not see
it — a psychologist the child is not assigned to, a psychologist taking over
from a colleague whose history is hidden, or a role that may read a record but
not write it? And, with that settled, how could it serve each role better
without becoming less reliable?

## What each role can get, compared with its screen

"Screen" is what the same person already sees without the assistant. The rule
the assistant must hold is: **never more than the screen, and never a different
number from the screen it links to.**

| Feature | ISA (Administrator) | Psychologist | SW (Staff) | Matches the screen? |
|---|---|---|---|---|
| Chat: schedule | agency | own sessions | own children's sessions, others as `C-0042` | yes — `scheduling/visibility.py` |
| Chat: statistics | agency | own caseload | own records | Dashboard yes; Agency Summary **differed**, now says so |
| Chat: concern search | all | assigned | own records | yes — names only |
| Chat: child summary (5 remarks) | all remarks | own remarks when history is hidden | all remarks | yes — `ChildReportView` |
| Chat: care gaps | agency | assigned | own records | yes — Monitoring |
| Chat: self-report flags (child's words) | all | assigned, history ignored by design | own records | yes |
| Chat: unassigned children | agency | always empty | own records | **empty sentence was wrong**, fixed |
| Pre-session brief | per child | per child, history rule applied | per child | **cache leaked across users**, fixed |
| Document summary: draft | any document | own child's reports | own records' referrals | **any reader could overwrite**, fixed |
| Document summary: confirm | same | same, history rule applied | same | **same, plus no history check**, fixed |
| Remark polish | the text being typed | the text being typed | — | yes |

The ten chatbot tools were sound: every one takes scope from `request.user`
through `accounts/scoping.py`, and no argument can widen it. The problems were
all in the drafting half, where results are stored and served again.

## Found and fixed

### 1. A brief was cached per child, not per person

`LatestBriefView` served "today's brief for child 12" to whoever asked, and
`PrefetchBriefsView` skipped any child somebody had briefed that day. A brief is
written from what its requester may see — `_brief_only_author` removes other
authors' remarks when `assignee_sees_history` is off — so:

- an ISA opens the child in the morning; the brief contains every remark;
- the newly assigned psychologist, whose screen hides the previous
  psychologist's notes, clicks **Pre-session brief** and is served the ISA's.

The same happened with the previous psychologist's own brief on the morning of
a reassignment. Now a brief is served only to the person it was drafted for
(`_todays_briefs(user)`), and prefetch keys on (user, child). Review found one
more case: a psychologist's own brief drafted while history was carried, still
served after the ISA hid it. Where history is hidden, a brief older than the
child record's last change is now drafted again (`_current_brief`).

### 2. A summary is a write, and it was checked as a read

Drafting a summary replaces `ai_summary` — the screen warns a confirmed one
"cannot be recovered" — and confirming saves text as the psychologist's own.
Both endpoints only checked that the caller could *read* the document, and the
button was shown to everyone, so:

- a social worker (read-only on psychological reports) could replace the
  psychologist's confirmed summary, or confirm their own words in its place;
- a psychologist (read-only on case referrals) could do the same to the
  social worker's;
- confirming had lost the carry-history check, so a psychologist could
  overwrite the summary of a report the screen does not even show them.

Both now go through `_document_to_summarise`, which applies the document's own
write rule — reports: `is_admin_or_assignee`, the clinical viewsets' rule;
referrals: `writes_case_referrals`, the referral viewset's rule — plus the
history check. The buttons are hidden from anyone the server would refuse.
Review then found the API still handed every reader the unconfirmed draft
the screen hid; the report and referral serializers now return a draft only
to someone who may confirm it, and a confirmed summary to everyone.

### 3. "Only the chatbot is hosted" was written down and not enforced

`CLAUDE.md`, `CLOUD-DEPLOYMENT.md` and the deployment plan all say briefs,
polish, summaries and the self-report check stay off a hosted model. In code,
`get_ai_client()` handed the hosted client to every caller, so with
`ASSISTANT_ALLOW_HOSTED_MODEL` on, case notes, whole reports and a child's own
survey answers went to the hosted provider.

`get_ai_client()` now refuses a hosted model unless the caller passes
`allow_hosted=True`, and only the chatbot, the two administrator probes,
`ai_check` and `ai_eval` do. A drafting feature on a hosted deployment answers
503 with the reason rather than falling back to `ollama_url`, which an
administrator can edit.

**On the demo this switches the drafting features off**, as the documents
already said it was. Nothing is deployed until this branch reaches `cloud-setup`.

### 4. Two answers that disagreed with their screen for one role

- A social worker's statistics count their own records but link to the Agency
  Summary, which counts the agency. The answer now says so.
- "Which children have no psychologist?" is always empty for a psychologist,
  and told them "Every active child has a psychologist assigned" — a claim
  about the agency from a query that cannot see it. Each role is now told what
  was actually checked.

## Found outside the assistant, and fixed too

**The carry-history control was applied on the child's page and nowhere else.**
With `assignee_sees_history` off, the page hid the previous psychologist's
records, but the endpoints behind it served them:

- `GET /api/remarks/?child=<id>` returned the previous psychologist's notes,
  and `/report-files/`, `/interviews/`, `/treatment-plans/`, `/result-entries/`
  and `/pre-assessments/` did the same through their shared base class;
- the previous psychologist's report could be downloaded and read as text;
- their note could be **edited** — the write check lets the child's
  psychologist edit any of the child's records;
- Monitoring printed each child's latest remark and classification whoever
  wrote them, and counted reports the page did not list.

The rule is now `accounts.scoping.hide_earlier_history`, beside
`scope_to_visible`, and every one of those readers goes through it — the
child's page included, so the page and the endpoints cannot disagree again.
The record base class hides by default; problems and consents opt out, as the
page always had them. `clinical/tests/test_carry_history.py` holds each door,
and a browser check on 27 Sep found the planted note in none of the 33 API
responses behind the child's page, Monitoring and Results & Reports with
history hidden, and in all of them with it carried.

Review of the first version found what was still worked out from the hidden
pre-assessments: the child's pre-assessment status ("Answered" beside an empty
list), the instruments used, and Monitoring's pre-assessment count and last
activity. Those now come from `accounts.scoping.visible_pre_assessments`, a
filtered prefetch used by the child's page, the children API and Monitoring.
Business rules that load a child without it still see every pre-assessment.

Care-gap alerts were left as they are: they are about the agency's process,
not a colleague's findings, and "pre-assessment overdue" on a child the agency
has already assessed would send the psychologist to repeat it.

## Next steps, role by role

Ranked by how much each helps against how much it risks. The chatbot's routing
has a measured ceiling (ten tools; `qwen2.5:3b` misroutes 3/87 there, and four
rounds of tuning could not hold it), and descriptions interact globally, so the
cheapest ideas are the ones that **add no tool and change no prompt**.

### No model at all — do these first

1. **Deterministic brief facts, shown above the prose.** Next session and its
   purpose, days since the last one, open problems, the active treatment plan's
   objectives, unreviewed self-report flags (counted, not quoted), care gaps.
   Every item is a query. It makes the brief useful when the model is off,
   hosted, or slow, and gives the psychologist something to check the prose
   against. The prose is unchanged, so no `ai_eval` rerun is needed.
2. **Role-aware care gaps inside `list_care_gaps`, not as new tools.** A social
   worker's gaps are not a psychologist's: no case referral on file (booking is
   refused without one), survey link unanswered after 7 days, consent missing,
   no psychologist yet. A psychologist's stay as they are. The routing and the
   tool description stay the same; only the rules behind it change with the role.
3. **The panel knows the deployment.** Have `/assistant/capabilities/` report
   `drafting: false` on a hosted deployment, and hide the brief, polish and
   summary buttons there. The server already refuses; this saves a click that
   can only fail.
4. **An assistant access log per child, for the ISA.** Every brief and summary
   already writes an `AssistantJob` naming its user and `child:<id>`. Listing
   them on the child's page answers "who had the model read this child's notes"
   without a new table.

### A prompt change — each needs its own `ai_eval` run

5. **Brief from more than remarks.** Feed the deterministic facts from (1) into
   the prompt as well. Risk: more facts is more to invent around; measure it
   with `ai_eval --feature brief`.
6. **A referral-reading brief for social workers.** Their question before a
   home visit is "what did the referral say and what is booked", not "what did
   the psychologist observe". Built from the confirmed referral summary and
   the schedule. Needs its own measurement.

### A new chatbot tool — only with evidence

7. Read the ISA's **Unanswered questions** card first. It lists what people
   asked and the assistant could not answer, grouped by role. A tool earns its
   place from that list, and each addition gets its own `ai_eval` run, as the
   last four did. The best candidate on paper is "which of my children have no
   referral" (social worker). Build it as a role rule in (2) rather than a tool
   unless the card shows people ask it in words that route elsewhere.

### Decisions for the owner

- **Should social workers get pre-session briefs?** They can today. The brief
  only uses the remarks they can already read on the Remarks tab, so nothing
  leaks. But its instructions say it is "for a licensed psychologist before a
  session", and a social worker does not hold one. Either hide the button from
  staff, or build (6) for them.
- **Should a reader who cannot write be able to *read* an AI summary draft
  without saving it?** At present, anyone who cannot write the document sees
  only the confirmed summary. A read-only draft, returned and not stored, would
  give them the reading aid without the write. It is cheap to build, but it
  is a second kind of summary that people will have to tell apart.
