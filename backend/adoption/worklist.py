"""What needs a person today, and the four numbers above it.

Spec section 5.8 is emphatic that this is "not a notification feed - a ranked
worklist, max five items". The cap is the design. A panel listing forty
problems is a panel nobody reads, and the entire value of this module is that
somebody deals with the top item before a statutory window shuts.

Cases on hold and closed cases are excluded throughout. A frozen case is
frozen on purpose, and ranking it as overdue would fill the list with problems
nobody is permitted to fix yet.
"""
from datetime import timedelta

from django.utils import timezone

from adoption import status as case_status
from adoption.models import AdoptionCase, AdoptionStage

MAX_ITEMS = 5
TRIAL_CUSTODY_STAGE = 6
CDCLAA_STAGE = 2
# How far ahead an expiring family credential starts mattering. The spec's
# notification rule uses the same window, so the panel and the mail agree.
CREDENTIAL_WARNING_DAYS = 60

# Severity, worst first. This ordering IS the specification: a missed statutory
# window is not the same class of problem as a slow month, and sorting them
# together by age would bury it.
SEVERITY = {
    "clock_expired": 0,
    "trial_custody_exceeded": 1,
    "credentials_expiring": 2,
    "stage_over_target": 3,
    "consent_missing": 4,
}


def open_cases():
    return (AdoptionCase.objects
            .filter(closed_at__isnull=True, on_hold=False)
            .select_related("child", "current_stage", "owner", "pap")
            .prefetch_related("requirements", "clocks"))


def _item(case, kind, message, requirement_code=""):
    return {
        "kind": kind,
        "severity": SEVERITY[kind],
        "case_id": case.id,
        "child_id": case.child_id,
        "child_name": case.child.fullname,
        "stage": case.current_stage.number,
        "stage_name": case.current_stage.name,
        "days_in_stage": case_status.days_in_stage(case),
        "message": message,
        "requirement_code": requirement_code,
    }


def needs_attention(cases=None):
    """The ranked worklist: severity first, then the oldest of each kind."""
    today = timezone.localdate()
    items = []

    for case in (cases if cases is not None else open_cases()):
        for clock in case.clocks.all():
            if clock.satisfied_at:
                continue
            left = case_status.clock_days_left(clock, today)
            if left < 0:
                items.append(_item(
                    case, "clock_expired",
                    f"{clock.label} ran out {abs(left)} days ago."))

        target = case.current_stage.target_days
        elapsed = case_status.days_in_stage(case)

        if case.current_stage.number == TRIAL_CUSTODY_STAGE and target and elapsed > target:
            items.append(_item(
                case, "trial_custody_exceeded",
                f"Supervised trial custody has run {elapsed} days, past the {target}-day window."))
        elif target and elapsed > target:
            items.append(_item(
                case, "stage_over_target",
                f"{case.current_stage.name} has run {elapsed} days against a {target}-day target."))

        pap = case.pap
        if pap:
            for label, expiry in (("CEA", pap.cea_expiry),
                                  ("Home study", pap.home_study_expiry)):
                if expiry and (expiry - today).days <= CREDENTIAL_WARNING_DAYS:
                    items.append(_item(
                        case, "credentials_expiring",
                        f"{pap.family_name}: {label} expires {expiry:%d %b %Y}."))

        # Consent is pending on every case the moment it is admitted, so
        # flagging it immediately would put an item on this list for every
        # single admission - the "panel nobody reads" failure, on day one. It
        # becomes worth someone's attention only once the stage is running out
        # of room for it.
        consent = next((r for r in case.requirements.all()
                        if r.code == "consent_on_file"), None)
        if (consent is not None and not consent.satisfied
                and case.current_stage.number == 1
                and target and elapsed > target / 2):
            items.append(_item(
                case, "consent_missing",
                f"Guardian or child consent is still not on file after {elapsed} days.",
                requirement_code="consent_on_file"))

    items.sort(key=lambda i: (i["severity"], -i["days_in_stage"]))
    return items[:MAX_ITEMS]


def kpis():
    """The four counts above the board. Every one of them is a filter.

    Spec section 5.2: "Each tile is a link that applies the equivalent filter
    to the board - never a dead number."
    """
    live = list(open_cases())
    trial = [c for c in live if c.current_stage.number == TRIAL_CUSTODY_STAGE]
    trial_target = (AdoptionStage.objects
                    .filter(number=TRIAL_CUSTODY_STAGE)
                    .values_list("target_days", flat=True).first() or 0)

    return {
        "open_cases": len(live),
        "at_cdclaa": len([c for c in live if c.current_stage.number == CDCLAA_STAGE]),
        "in_trial_custody": len(trial),
        "trial_custody_overrun": len([
            c for c in trial
            if trial_target and case_status.days_in_stage(c) > trial_target]),
        "finalized_this_year": AdoptionCase.objects.filter(
            closure_reason=AdoptionCase.FINALIZED,
            closed_at__year=timezone.localdate().year).count(),
    }
