from rest_framework import serializers
from django.contrib.auth import get_user_model
from django.utils import timezone
from accounts.display import display_name
from accounts.models import Role
from children.models import Child

User = get_user_model()



class ChildSerializer(serializers.ModelSerializer):
    # Frontend uses `psychologist`; map it to the assigned_psychologist FK.
    psychologist = serializers.PrimaryKeyRelatedField(
        source="assigned_psychologist", queryset=User.objects.all(),
        required=False, allow_null=True,
    )
    psychologist_name = serializers.CharField(
        source="assigned_psychologist.fullname", read_only=True, default=None,
    )

    # Declared explicitly (not auto-generated from the model's `choices=`) so
    # the automatic ChoiceField membership check does NOT run before our own
    # validate_case_category — that check happens first, in field-level
    # to_internal_value, and would reject an unchanged legacy value (e.g.
    # "Trafficked", from before the Category list was narrowed) on every
    # future edit before validate_case_category ever got a chance to apply
    # its change-only exemption.
    case_category = serializers.CharField(required=False, allow_blank=True)

    termination = serializers.SerializerMethodField()
    terminations = serializers.SerializerMethodField()
    # Computed V2 profile surface: has the pre-assessment been answered, and
    # which instrument titles were used (titles only — copyright policy).
    pre_assessment_status = serializers.SerializerMethodField()
    instruments_used = serializers.SerializerMethodField()
    has_case_referral = serializers.SerializerMethodField()

    class Meta:
        model = Child
        fields = [
            "id", "first_name", "middle_initial", "last_name", "fullname", "birth_date", "gender",
            "province", "municipality", "barangay", "address",
            "psgc_province", "psgc_municipality", "psgc_barangay",
            "case_type", "case_category", "surrendered_by", "status", "case_status", "assignee_sees_history",
            "place_of_birth_or_found", "birth_status", "legal_status",
            "date_of_admission", "date_of_placement_to_custodian", "type_of_adoption",
            "photo", "referral_source", "referral_reason",
            "education_level", "current_placement", "medical_notes", "recommendation",
            "psychologist", "psychologist_name",
            "termination", "terminations",
            "pre_assessment_status", "instruments_used", "has_case_referral",
            "updated_at",
        ]
        # The tracker moves only through the advance-status / terminate actions.
        # fullname is derived (Child.save() composes it from the name parts).
        read_only_fields = ["case_status", "updated_at", "fullname"]

    def get_pre_assessment_status(self, obj):
        # 5-state pipeline status; see Child.pre_assessment_status.
        return obj.pre_assessment_status()

    def get_has_case_referral(self, obj):
        """Whether a session can be booked for this child at all.

        Booking refuses a child with no referral on file, and until this field
        existed the only way to discover that was to open the form, pick a day
        and read the refusal — the list and the drawer showed nothing.

        Reads the prefetch when the viewset supplied one rather than asking per
        row. The list returns the whole caseload on several screens, and a
        query per child to draw a chip is invisible here and obvious in a field
        office.
        """
        cached = getattr(obj, "_prefetched_objects_cache", {}).get("case_referrals")
        if cached is not None:
            return bool(cached)
        return obj.case_referrals.exists()

    def get_instruments_used(self, obj):
        titles = []
        for p in obj.pre_assessments.all():
            if p.status != "completed":
                continue
            for i in p.instruments.all():
                if i.title not in titles:
                    titles.append(i.title)
        return titles

    def get_termination(self, obj):
        if obj.status != Child.INACTIVE:
            return None
        t = obj.terminations.first()  # newest first (Meta.ordering)
        if not t:
            return None
        by = t.terminated_by
        return {
            "date": t.date,
            "reason_category": t.reason_category,
            "note": t.note,
            "terminated_by": display_name(by) or None,
        }

    def get_terminations(self, obj):
        out = []
        for t in obj.terminations.all():  # Meta.ordering: newest first
            by = t.terminated_by
            out.append({
                "date": t.date, "reason_category": t.reason_category, "note": t.note,
                "terminated_by": display_name(by) or None,
            })
        return out

    def _legacy_fullname_parts(self):
        # Back-compat: some callers (and older API integrations) still create
        # a child by sending only "fullname", with no first/last name parts.
        # fullname is read-only now, so it never reaches validated_data/attrs —
        # split it the same way the split_existing_fullnames data migration
        # does. str.split() with no args already discards whitespace-only
        # input (e.g. "   ".split() == []), so this never manufactures a name
        # out of blank space.
        legacy_fullname = (self.initial_data or {}).get("fullname")
        return str(legacy_fullname).split() if legacy_fullname else []

    def validate_birth_date(self, value):
        # The agency only serves children aged 5-17 (inclusive); this uses
        # an exact-birthday-aware age calculation, not a floor(days/365).
        # Re-validation is CHANGE-only (Task 13 lock: "edits stay
        # partial-friendly"): the frontend's edit form always resends the
        # existing birth_date on PUT (full-object update pattern), so
        # re-checking an UNCHANGED birth_date on every update would
        # permanently lock out ANY field edit on a child whose age has
        # since drifted outside 5-17 (e.g. a long-running case where the
        # child turned 18, or a legacy record with an unusual birth_date).
        # But a deliberate edit that actually changes birth_date must still
        # be range-checked - only pass unchanged values through untouched.
        if value is None:
            return value
        if self.instance is not None and value == self.instance.birth_date:
            return value
        today = timezone.localdate()
        age = today.year - value.year - ((today.month, today.day) < (value.month, value.day))
        if not (5 <= age <= 17):
            raise serializers.ValidationError(
                "The child must be between 5 and 17 years old.")
        return value

    def validate_case_category(self, value):
        # Task 13 lock ("edits stay partial-friendly") applies here too: the
        # frontend's edit form always resends the full object on PUT
        # (Children.jsx save()), including case_category unchanged. The
        # Category list was narrowed from 18 NACC-SAMD-GF-000 values down to
        # 6 (see CASE_CATEGORY_CHOICES) - a record still holding one of the
        # removed values (e.g. "Trafficked") would otherwise fail DRF's
        # ChoiceField validation on EVERY future edit, even ones that never
        # touch this field. Mirrors validate_birth_date's change-only guard:
        # pass an unchanged value through untouched, still enforce the
        # current choice list when the value is genuinely being changed.
        if self.instance is not None and value == self.instance.case_category:
            return value
        valid = {c[0] for c in Child.CASE_CATEGORY_CHOICES}
        if value and value not in valid:
            raise serializers.ValidationError(
                f'"{value}" is not a valid choice.')
        return value

    def create(self, validated_data):
        if not validated_data.get("first_name") and not validated_data.get("last_name"):
            parts = self._legacy_fullname_parts()
            if parts:
                validated_data["last_name"] = parts[-1] if len(parts) > 1 else ""
                validated_data["first_name"] = " ".join(parts[:-1]) if len(parts) > 1 else parts[0]
        return super().create(validated_data)

    def validate(self, attrs):
        request = self.context.get("request")
        role = getattr(getattr(getattr(request, "user", None), "role", None),
                       "role_name", None) if request else None
        if self.instance:
            # fullname is read-only (DRF drops it from `attrs`), so an attempt to
            # PATCH it has to be caught from the raw request payload instead.
            raw = self.initial_data if hasattr(self, "initial_data") else {}
            for f in ("first_name", "middle_initial", "last_name", "fullname"):
                if f in attrs:
                    new_val = attrs[f]
                elif f in raw:
                    new_val = raw[f]
                else:
                    continue
                if new_val != getattr(self.instance, f):
                    raise serializers.ValidationError(
                        {f: "The child's name cannot be changed after the record is created."})
            if role == Role.PSYCHOLOGIST:
                if ("assigned_psychologist" in attrs
                        and attrs["assigned_psychologist"] != self.instance.assigned_psychologist):
                    raise serializers.ValidationError(
                        {"psychologist": "Only administrators or staff can reassign a psychologist."})
                attrs.pop("assignee_sees_history", None)
                attrs.pop("status", None)
        else:
            # Create: fullname is read-only and first_name/last_name are
            # blank=True on the model, so DRF won't require any of them on
            # its own. Require a name in some form here — either the normal
            # first_name/last_name fields, or a legacy fullname-only payload
            # (the exact same acceptance test create()'s fallback uses, via
            # _legacy_fullname_parts() — .strip() here matches .split()'s
            # whitespace-only rejection there, so the two can't diverge).
            legacy_parts = self._legacy_fullname_parts()
            has_name = (
                (attrs.get("first_name") or "").strip()
                or (attrs.get("last_name") or "").strip()
                or legacy_parts
            )
            if not has_name:
                raise serializers.ValidationError(
                    {"first_name": "Provide a name - either first_name/last_name, or a legacy fullname."})
            # Task 13: the agency's standard intake requires first_name,
            # last_name, birth_date, gender, and case_type all together.
            # This does NOT apply to the legacy fullname-only back-compat
            # shape above (Task 1/12) — those callers never supply split
            # name parts at all, and existing integrations/tests rely on
            # that path staying lenient (see children/tests/test_api.py
            # and activity/tests/test_activity.py).
            if not legacy_parts:
                missing = {
                    f: "This field is required."
                    for f in ("first_name", "last_name", "birth_date", "gender", "case_type")
                    if not str(attrs.get(f) or "").strip()
                }
                if missing:
                    raise serializers.ValidationError(missing)
        return attrs

