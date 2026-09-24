"""V2 report endpoints: per-child chart view, cross-child monitoring, agency
summary, and the (interim) dashboard — all sourced from clinical records."""

from rest_framework import generics, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from accounts.display import display_name
from accounts.models import Role
from accounts.scoping import role_of as _role, scope_to_visible
from accounts.permissions import CanViewResults, IsAdminOrStaff
from children.models import Child, TerminationRecord
from children.serializers import ChildSerializer
from clinical import reports
from clinical.models import (
    PreAssessment, ResultEntry, RemarkNote, PsychologicalReport,
)
from clinical.serializers import (
    PreAssessmentSerializer, ResultEntrySerializer, RemarkNoteSerializer,
    TreatmentPlanSerializer, PsychologicalReportSerializer, ProblemEntrySerializer,
    CaseReferralSerializer, OpinionnaireInviteSerializer, ClinicalInterviewRecordSerializer,
    SelfReportFlagSerializer,
)




class ChildReportView(generics.GenericAPIView):
    """Chart view of one child: profile + pre-assessment log + clinical interviews
    + result entries + report files + remarks + treatment plan + open problems."""
    permission_classes = [CanViewResults]

    def get(self, request, child_id):
        try:
            child = Child.objects.prefetch_related("pre_assessments__instruments").get(pk=child_id)
        except Child.DoesNotExist:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        role = _role(request)
        if role == Role.PSYCHOLOGIST and child.assigned_psychologist_id != request.user.id:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)

        pas = child.pre_assessments.all().select_related("consent", "interview", "psychologist")
        results = child.result_entries.select_related("instrument", "entered_by")
        remarks = child.remarks.select_related("author")
        plans = child.treatment_plans.select_related("author")
        files = child.psych_reports.select_related("author")
        problems = child.problems.all()
        case_referrals = child.case_referrals.select_related("uploaded_by")
        opinionnaires = child.opinionnaire_invites.select_related("template")
        interviews = child.clinical_interviews.select_related("template", "interviewer")
        self_report_flags = child.self_report_flags.select_related("reviewed_by")

        # Carry-history control: a newly assigned psychologist without history
        # sees only records they authored themselves.
        #
        # self_report_flags is deliberately absent from this block. Self-reports
        # are the child's own words, not a colleague's prior opinions, and the
        # person responsible for her now must see them. The omission is a
        # decision, not an oversight.
        if role == Role.PSYCHOLOGIST and not child.assignee_sees_history:
            pas = pas.filter(psychologist=request.user)
            results = results.filter(entered_by=request.user)
            remarks = remarks.filter(author=request.user)
            plans = plans.filter(author=request.user)
            files = files.filter(author=request.user)
            interviews = interviews.filter(interviewer=request.user)

        return Response({
            "child": ChildSerializer(child).data,
            "pre_assessments": PreAssessmentSerializer(pas, many=True).data,
            "interviews": ClinicalInterviewRecordSerializer(interviews, many=True).data,
            "result_entries": ResultEntrySerializer(results, many=True).data,
            "remarks": RemarkNoteSerializer(remarks, many=True).data,
            "treatment_plans": TreatmentPlanSerializer(plans, many=True).data,
            "reports": PsychologicalReportSerializer(
                files, many=True, context={"request": request}).data,
            "problems": ProblemEntrySerializer(problems, many=True).data,
            "case_referrals": CaseReferralSerializer(case_referrals, many=True).data,
            "opinionnaires": OpinionnaireInviteSerializer(opinionnaires, many=True).data,
            "self_report_flags": SelfReportFlagSerializer(self_report_flags, many=True).data,
        })


class MonitoringListView(generics.GenericAPIView):
    """Cross-child monitoring table, role-scoped. V2 columns: last activity /
    next appointment / flags — no engine scores exist."""
    permission_classes = [CanViewResults]

    def get(self, request):
        children = (Child.objects.exclude(status=Child.INACTIVE)
                    .select_related("assigned_psychologist")
                    .prefetch_related("pre_assessments__instruments", "consents"))
        children = scope_to_visible(children, request, path=None)
        children = list(children)
        ids = [c.id for c in children]

        from django.utils import timezone as tz
        from scheduling.models import Appointment
        next_appt = {}
        for a in (Appointment.objects
                  .filter(child_id__in=ids, status=Appointment.SCHEDULED, start__gte=tz.now())
                  .order_by("start")):
            next_appt.setdefault(a.child_id, a.start)

        latest_result = {}
        for r in ResultEntry.objects.filter(child_id__in=ids).order_by("date", "id"):
            latest_result[r.child_id] = r
        # The note itself, not just its date: monitoring shows the psychologist's
        # most recent remark, and re-querying per row would be N+1.
        last_remark = {}
        for r in RemarkNote.objects.filter(child_id__in=ids).order_by("date", "id"):
            last_remark[r.child_id] = r
        report_counts = {}
        for r in PsychologicalReport.objects.filter(child_id__in=ids):
            report_counts[r.child_id] = report_counts.get(r.child_id, 0) + 1

        rows = []
        for c in children:
            completed = [p for p in c.pre_assessments.all() if p.status == "completed"]
            last_pa = max((p.date for p in completed), default=None)
            res = latest_result.get(c.id)
            remark = last_remark.get(c.id)
            candidates = [d for d in (last_pa,
                                      remark.date if remark else None,
                                      res.date if res else None) if d]
            last_activity = max(candidates) if candidates else None
            psy = c.assigned_psychologist
            rows.append({
                "child_id": c.id,
                "child_name": c.fullname,
                "case_ref": f"C-{c.id:04d}",
                "case_type": c.case_type or None,
                "psychologist_name": display_name(psy) or None,
                "case_status": c.case_status,
                "pre_assessment_status": c.pre_assessment_status(),
                "latest_classification": (res.classification or None) if res else None,
                "latest_remark": (remark.text or None) if remark else None,
                "last_activity": last_activity.isoformat() if last_activity else None,
                "next_session": (tz.localtime(next_appt[c.id]).strftime("%Y-%m-%d %H:%M")
                                 if c.id in next_appt else None),
                "report_count": report_counts.get(c.id, 0),
                "pre_assessment_count": len(completed),
            })
        rows.sort(key=lambda r: (r["child_name"] or "").lower())
        return Response(rows)


def _nacc_service_users():
    """Point-in-time census block mirroring the "Service Users" section of
    NACC-SAMD-GF-000 (June 2025). Always computed over ACTIVE children —
    unlike the rest of the summary, it is NOT filtered by the `range` param."""
    from django.utils import timezone as tz
    # The bands live in clinical.reports because the assistant's statistics
    # use the same rule, and must never put a child in a different band.
    from clinical.reports import AGE_BANDS, UNSPECIFIED_AGE, age_band
    today = tz.localdate()

    age_rows = {label: {"label": label, "male": 0, "female": 0, "total": 0}
                for label, _, _ in AGE_BANDS}
    unspecified_age_row = {"label": UNSPECIFIED_AGE, "male": 0, "female": 0, "total": 0}
    has_unspecified_age = False

    category_counts = {}
    unspecified_category_count = 0

    for c in Child.objects.filter(status=Child.ACTIVE):
        label = age_band(c.birth_date, today)
        if label == UNSPECIFIED_AGE:
            row = unspecified_age_row
            has_unspecified_age = True
        else:
            row = age_rows[label]
        if c.gender == "Male":
            row["male"] += 1
        elif c.gender == "Female":
            row["female"] += 1
        row["total"] += 1

        if c.case_category:
            category_counts[c.case_category] = category_counts.get(c.case_category, 0) + 1
        else:
            unspecified_category_count += 1

    age_groups = [age_rows[label] for label, _, _ in AGE_BANDS]
    if has_unspecified_age:
        age_groups.append(unspecified_age_row)

    # Preserve the official form order; drop zero-count categories.
    case_categories = [
        {"label": label, "count": category_counts[label]}
        for label, _ in Child.CASE_CATEGORY_CHOICES
        if category_counts.get(label)
    ]
    if unspecified_category_count:
        case_categories.append({"label": "Unspecified", "count": unspecified_category_count})

    return {"age_groups": age_groups, "case_categories": case_categories}


def _summary_csv(data):
    import csv
    from django.http import HttpResponse
    resp = HttpResponse(content_type="text/csv")
    resp["Content-Disposition"] = 'attachment; filename="agency-summary.csv"'
    w = csv.writer(resp)
    w.writerow(["Metric", "Value"])
    w.writerow(["Completed pre-assessments", data["total"]])
    w.writerow(["Children seen", data["children"]])
    w.writerow(["Pending pre-assessments", data["pending_pre_assessments"]])
    w.writerow([])
    w.writerow(["Case type", "Count"])
    for k, v in data["by_case_type"].items():
        w.writerow([k, v])
    w.writerow([])
    w.writerow(["Psychologist", "Sessions"])
    for p in data["per_psychologist"]:
        w.writerow([p["name"], p["count"]])
    w.writerow([])
    w.writerow(["Termination reason", "Count"])
    for k, v in data["terminations_by_reason"].items():
        w.writerow([k, v])
    w.writerow([])
    att = data["attendance"]
    w.writerow(["Attendance", "Count"])
    for label, key in (("Completed", "completed"), ("No-show", "no_show"),
                       ("Not yet recorded", "unrecorded"), ("Upcoming", "upcoming"),
                       ("Cancelled (not counted)", "cancelled")):
        w.writerow([label, att[key]])
    w.writerow(["No-show rate (%)", "" if att["no_show_rate"] is None else att["no_show_rate"]])
    w.writerow([])
    wait = data["first_session_wait"]
    w.writerow(["Wait for a first session", "Value"])
    for label, key in (("Children seen for a first session", "seen"),
                       ("Median days from record created to first completed session",
                        "median_days"),
                       ("Longest wait (days)", "longest_days"),
                       ("Active children still waiting", "waiting"),
                       ("Longest wait so far (days)", "longest_waiting_days"),
                       ("First session dated before the record (not counted)",
                        "before_record")):
        w.writerow([label, "" if wait[key] is None else wait[key]])
    w.writerow([])
    dur = data["pre_assessment_duration"]
    w.writerow(["Time in pre-assessment", "Value"])
    for label, key in (("Pre-assessments completed", "completed"),
                       ("Median days from start date to completion", "median_days"),
                       ("Longest (days)", "longest_days"),
                       ("Still open (not counted)", "open"),
                       ("Completed with no completion date (not counted)",
                        "no_completion_date"),
                       ("Completed before the start date (not counted)",
                        "completed_before_start")):
        w.writerow([label, "" if dur[key] is None else dur[key]])
    w.writerow([])
    w.writerow(["NACC Service Users by Age Group"])
    w.writerow(["Age Group", "Male", "Female", "Total"])
    for row in data["nacc_service_users"]["age_groups"]:
        w.writerow([row["label"], row["male"], row["female"], row["total"]])
    w.writerow([])
    w.writerow(["NACC Service Users by Case Category"])
    w.writerow(["Category", "Count"])
    for row in data["nacc_service_users"]["case_categories"]:
        w.writerow([row["label"], row["count"]])
    return resp


class SummaryReportView(generics.GenericAPIView):
    """Agency Summary (admin + staff): census KPIs over a date range."""
    permission_classes = [IsAdminOrStaff]

    def get(self, request):
        rng = request.query_params.get("range", "monthly")
        qs = (PreAssessment.objects.filter(status=PreAssessment.COMPLETED)
              .select_related("child", "psychologist").order_by("date", "id"))
        frm, to = request.query_params.get("from"), request.query_params.get("to")
        if frm:
            qs = qs.filter(date__gte=frm)
        if to:
            qs = qs.filter(date__lte=to)
        data = reports.summary(list(qs), rng)

        term_qs = TerminationRecord.objects.all()
        if frm:
            term_qs = term_qs.filter(date__gte=frm)
        if to:
            term_qs = term_qs.filter(date__lte=to)
        by_reason = {}
        for t in term_qs:
            by_reason[t.reason_category] = by_reason.get(t.reason_category, 0) + 1
        data["terminations_by_reason"] = by_reason
        data["pending_pre_assessments"] = (PreAssessment.objects
                                           .exclude(status=PreAssessment.COMPLETED).count())
        # Per-psychologist active caseload (children assigned).
        caseload = {}
        for c in (Child.objects.filter(status=Child.ACTIVE)
                  .select_related("assigned_psychologist")):
            if not c.assigned_psychologist_id:
                continue
            name = display_name(c.assigned_psychologist)
            caseload[name] = caseload.get(name, 0) + 1
        data["caseload_per_psychologist"] = [
            {"name": k, "caseload": v}
            for k, v in sorted(caseload.items(), key=lambda kv: -kv[1])]
        data["nacc_service_users"] = _nacc_service_users()

        # Attendance over the same from/to window as the rest of the summary,
        # by the one definition in clinical.reports that the assistant's
        # statistics also use. Agency-wide: this screen is Admin and Staff.
        from django.utils import timezone as tz
        from scheduling.models import Appointment
        appts = Appointment.objects.only("status", "start")
        if frm:
            appts = appts.filter(start__date__gte=frm)
        if to:
            appts = appts.filter(start__date__lte=to)
        data["attendance"] = reports.attendance(appts, tz.now())

        # The wait for a first session, over the same window, counted by the
        # day it ended. `to` is inclusive here and the definition's end is not.
        from datetime import date, timedelta
        data["first_session_wait"] = reports.first_session_wait(
            Child.objects.all(), tz.localdate(),
            start=date.fromisoformat(frm) if frm else None,
            end=date.fromisoformat(to) + timedelta(days=1) if to else None)
        # Time in pre-assessment, the same window, by the day each was completed.
        data["pre_assessment_duration"] = reports.pre_assessment_duration(
            PreAssessment.objects.all(),
            start=date.fromisoformat(frm) if frm else None,
            end=date.fromisoformat(to) + timedelta(days=1) if to else None)

        # NB: `format` is reserved by DRF content negotiation, so use `export`.
        if request.query_params.get("export") == "csv":
            return _summary_csv(data)
        return Response(data)


class DashboardView(generics.GenericAPIView):
    """Census Dashboard (athena pattern): census counts, today's schedule
    strip, availability at a glance, intake vs termination trend, pending
    pre-assessments, deterministic care-gap alerts — all role-scoped."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        from django.utils import timezone as tz
        from clinical.care_gaps import compute_alerts
        from scheduling.models import Appointment, AvailabilityBlock

        role = _role(request)
        rng = request.query_params.get("range", "monthly")

        scoped_children = Child.objects.all()
        pas = (PreAssessment.objects.filter(status=PreAssessment.COMPLETED)
               .select_related("child", "psychologist"))
        appts_today = (Appointment.objects
                       .filter(start__date=tz.localdate())
                       .exclude(status=Appointment.CANCELLED)
                       .select_related("child", "psychologist")
                       .prefetch_related("child__case_referrals__uploaded_by")
                       .order_by("start"))
        pending = PreAssessment.objects.exclude(status=PreAssessment.COMPLETED)
        blocks = AvailabilityBlock.objects.filter(active=True).select_related("psychologist")
        scoped_children = scope_to_visible(scoped_children, request, path=None)
        pas = scope_to_visible(pas, request)
        pending = scope_to_visible(pending, request)
        # Appointments and availability belong to the psychologist directly,
        # not through a child, so they are a different rule and stay here.
        if role == Role.PSYCHOLOGIST:
            appts_today = appts_today.filter(psychologist=request.user)
            blocks = blocks.filter(psychologist=request.user)
        pas = list(pas.order_by("date", "id"))

        # Fetched once and kept. This is the endpoint CensusContext calls on
        # every app load and every range change, and `active` used to be a
        # queryset that got iterated for the census below AND counted three
        # separate times in the response - four trips for one set of rows,
        # each of them a hop to another region on the hosted deployment.
        active_rows = list(scoped_children.filter(status=Child.ACTIVE)
                           .select_related("assigned_psychologist"))
        active_count = len(active_rows)
        inactive_count = scoped_children.filter(status=Child.INACTIVE).count()

        # Census: ACTIVE children per case type (the interview's
        # "active/adoption, active/foster care" view) + case-tracker stages.
        census_by_case_type = {}
        by_case_status = {Child.STAGE_PRE_ASSESSMENT: 0, Child.STAGE_COUNSELING: 0}
        counseling_per_psy = {}
        for c in active_rows:
            ct = c.case_type or "Unspecified"
            census_by_case_type[ct] = census_by_case_type.get(ct, 0) + 1
            if c.case_status in by_case_status:
                by_case_status[c.case_status] += 1
            if c.case_status == Child.STAGE_COUNSELING and c.assigned_psychologist_id:
                name = display_name(c.assigned_psychologist)
                counseling_per_psy[name] = counseling_per_psy.get(name, 0) + 1

        # Intake vs termination trend (follows the range selector, last 6 buckets).
        intake, term = {}, {}
        for c in scoped_children:
            b = reports.bucket(c.created_at.date(), rng)
            intake[b] = intake.get(b, 0) + 1
        term_qs = TerminationRecord.objects.all()
        term_qs = scope_to_visible(term_qs, request)
        for t in term_qs:
            b = reports.bucket(t.date, rng)
            term[b] = term.get(b, 0) + 1
        months = sorted(set(intake) | set(term))[-6:]
        intake_vs_termination = [
            {"bucket": m, "intake": intake.get(m, 0), "terminations": term.get(m, 0)}
            for m in months]

        today_weekday = tz.localdate().weekday()
        availability_today = [{
            "psychologist": display_name(b.psychologist),
            "start": str(b.start_time)[:5], "end": str(b.end_time)[:5],
            "capacity": b.capacity,
        } for b in blocks
            if (b.date == tz.localdate()) or (b.date is None and b.weekday == today_weekday)]

        def age(c):
            if not c.birth_date:
                return None
            days = (tz.localdate() - c.birth_date).days
            return max(0, days // 365)

        # The same rule as the calendar (scheduling/visibility.py): a social
        # worker sees the name only of a child they referred.
        from scheduling import visibility
        schedule_strip = [{
            "id": a.id,
            "child_id": a.child_id,
            **visibility.who(request.user, a.child),
            "age": age(a.child),
            "time": tz.localtime(a.start).strftime("%H:%M"),
            "purpose": a.purpose,
            "status": a.status,
            "psychologist": display_name(a.psychologist),
        } for a in appts_today]

        agg = reports.summary(pas, rng)
        return Response({
            "census": {
                "active": active_count,
                "inactive": inactive_count,
                "by_case_type": census_by_case_type,
                "by_case_status": by_case_status,
            },
            "counseling_per_psychologist": [
                {"name": k, "count": v}
                for k, v in sorted(counseling_per_psy.items(), key=lambda kv: -kv[1])],
            "total_children": active_count,
            "unassessed": max(0, active_count - len({p.child_id for p in pas})),
            "pending_pre_assessments": pending.count(),
            "today_schedule": schedule_strip,
            "availability_today": availability_today,
            "intake_vs_termination": intake_vs_termination,
            "trend": agg["trend"][-6:],
            "per_psychologist": agg["per_psychologist"],
            "by_case_type": agg["by_case_type"],
            "care_gaps": compute_alerts(scoped_children),
        })
