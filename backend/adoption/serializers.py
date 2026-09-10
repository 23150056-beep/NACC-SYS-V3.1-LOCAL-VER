"""What the adoption screens read.

Every derived field here is computed at serialization time from `status.py` and
`docket.py` - none of it is stored. That is the spec's rule and it is the
reason a chip on the board can never disagree with the docket behind it.
"""
from rest_framework import serializers

from adoption import docket, status as case_status
from adoption.models import (
    AdoptionCase, AdoptionStage, ComplianceClock, Handoff, PAP, Requirement, StageEvent,
)


class StageSerializer(serializers.ModelSerializer):
    class Meta:
        model = AdoptionStage
        fields = ["id", "number", "name", "owner_role", "target_days"]


class RequirementSerializer(serializers.ModelSerializer):
    stage_number = serializers.IntegerField(source="stage.number", read_only=True)
    submitted_by_name = serializers.CharField(
        source="submitted_by.fullname", read_only=True, default=None)
    verified_by_name = serializers.CharField(
        source="verified_by.fullname", read_only=True, default=None)
    document_name = serializers.SerializerMethodField()

    class Meta:
        model = Requirement
        fields = [
            "id", "stage_number", "code", "label", "state", "owned_externally",
            "due_date", "original_filename", "document_name",
            # The id as well as the name: the screen hides Verify on your own
            # upload, and matching on a display name works until two people
            # share one.
            "submitted_by", "submitted_by_name", "submitted_at",
            "verified_by_name", "verified_at", "waiver_reason",
        ]

    def get_document_name(self, obj):
        return obj.original_filename or (obj.document.name.rsplit("/", 1)[-1]
                                         if obj.document else None)


class StageEventSerializer(serializers.ModelSerializer):
    from_stage_number = serializers.IntegerField(
        source="from_stage.number", read_only=True, default=None)
    to_stage_number = serializers.IntegerField(source="to_stage.number", read_only=True)
    actor_name = serializers.CharField(source="actor.fullname", read_only=True, default=None)

    class Meta:
        model = StageEvent
        fields = ["id", "from_stage_number", "to_stage_number", "actor_name",
                  "occurred_at", "note", "direction"]


class PAPSerializer(serializers.ModelSerializer):
    class Meta:
        model = PAP
        fields = ["id", "family_name", "address", "region", "cea_number", "cea_expiry",
                  "home_study_expiry", "children_in_household", "status", "notes"]


class ClockSerializer(serializers.ModelSerializer):
    days_left = serializers.SerializerMethodField()
    total_days = serializers.SerializerMethodField()

    class Meta:
        model = ComplianceClock
        fields = ["id", "code", "label", "started_at", "duration_days",
                  "extension_days", "satisfied_at", "days_left", "total_days"]

    def get_days_left(self, obj):
        return case_status.clock_days_left(obj)

    def get_total_days(self, obj):
        # The bar re-scales on an extension rather than resetting, so the
        # original overrun stays visible.
        return obj.duration_days + obj.extension_days


class HandoffSerializer(serializers.ModelSerializer):
    child_name = serializers.CharField(source="child.fullname", read_only=True)
    released_by_name = serializers.CharField(
        source="released_by.fullname", read_only=True, default=None)
    case_type = serializers.CharField(source="child.case_type", read_only=True)

    class Meta:
        model = Handoff
        fields = ["id", "child", "child_name", "case_type",
                  "released_by_name", "released_at"]


class CaseCardSerializer(serializers.ModelSerializer):
    """One board card / list row. Everything on it is derived."""

    child_name = serializers.CharField(source="child.fullname", read_only=True)
    stage = serializers.IntegerField(source="current_stage.number", read_only=True)
    stage_name = serializers.CharField(source="current_stage.name", read_only=True)
    stage_target_days = serializers.IntegerField(
        source="current_stage.target_days", read_only=True, default=None)
    owner_name = serializers.CharField(source="owner.fullname", read_only=True, default=None)
    pap_name = serializers.CharField(source="pap.family_name", read_only=True, default=None)
    status = serializers.SerializerMethodField()
    days_in_stage = serializers.SerializerMethodField()
    progress_percent = serializers.SerializerMethodField()
    next_action = serializers.SerializerMethodField()

    class Meta:
        model = AdoptionCase
        fields = [
            "id", "child", "child_name", "stage", "stage_name", "stage_target_days",
            "owner_name", "pap_name", "on_hold", "closed_at", "closure_reason",
            "opened_at", "stage_entered_at",
            "status", "days_in_stage", "progress_percent", "next_action",
        ]

    def get_status(self, obj):
        return case_status.of(obj)

    def get_days_in_stage(self, obj):
        return case_status.days_in_stage(obj)

    def get_progress_percent(self, obj):
        return case_status.progress_percent(obj)

    def get_next_action(self, obj):
        return docket.next_action(obj)


class CaseDetailSerializer(CaseCardSerializer):
    """The child view: the case, its whole docket, its history and its clocks."""

    requirements = RequirementSerializer(many=True, read_only=True)
    events = StageEventSerializer(many=True, read_only=True)
    clocks = ClockSerializer(many=True, read_only=True)
    pap = PAPSerializer(read_only=True)
    blockers = serializers.SerializerMethodField()
    timeline = serializers.SerializerMethodField()
    child_ref = serializers.SerializerMethodField()
    endorsed_by = serializers.SerializerMethodField()

    class Meta(CaseCardSerializer.Meta):
        fields = CaseCardSerializer.Meta.fields + [
            "child_ref", "endorsed_by", "requirements", "events", "clocks", "pap",
            "blockers", "timeline",
        ]

    def get_child_ref(self, obj):
        return f"C-{obj.child_id:04d}"

    def get_endorsed_by(self, obj):
        handoff = obj.child.adoption_handoffs.first()
        if not handoff:
            return None
        return {
            "name": getattr(handoff.released_by, "fullname", None),
            "released_at": handoff.released_at,
        }

    def get_blockers(self, obj):
        return RequirementSerializer(docket.blockers_for_case(obj), many=True).data

    def get_timeline(self, obj):
        """All eight stages, always - so the road ahead is visible.

        Past stages carry the real date they were left, read from the
        append-only event log rather than from any column on the case.
        """
        left_at = {}
        for event in obj.events.all():
            if event.direction == StageEvent.ADVANCE and event.from_stage_id:
                left_at[event.from_stage.number] = event.occurred_at

        current = obj.current_stage.number
        out = []
        for stage in AdoptionStage.objects.all():
            out.append({
                "number": stage.number,
                "name": stage.name,
                "owner_role": stage.owner_role,
                "target_days": stage.target_days,
                "state": ("done" if stage.number < current
                          else "current" if stage.number == current else "ahead"),
                "completed_at": left_at.get(stage.number),
            })
        return out
