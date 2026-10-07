from rest_framework import serializers
from django.contrib.auth import get_user_model
from django.utils import timezone
from accounts.display import display_name
from accounts.models import Role
from accounts.phone import as_typed as phone_as_typed
from children import assignment, custodian, intake
from children.models import AssignmentRequest, Child

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
    # Whose record this is (accounts/scoping.py). Staff never choose it: a new
    # record is theirs (ChildViewSet.perform_create) and only an administrator
    # moves one. Must be a staff account, or empty.
    social_worker = serializers.PrimaryKeyRelatedField(
        queryset=User.objects.all(), required=False, allow_null=True,
    )
    social_worker_name = serializers.SerializerMethodField()

    # Declared explicitly (not auto-generated from the model's `choices=`) so
    # the automatic ChoiceField membership check does NOT run before our own
    # validate_case_category — that check happens first, in field-level
    # to_internal_value, and would reject an unchanged legacy value (e.g.
    # "Trafficked", from before the Category list was narrowed) on every
    # future edit before validate_case_category ever got a chance to apply
    # its change-only exemption.
    case_category = serializers.CharField(required=False, allow_blank=True)
    # The same, for the two lists that retired values on 24 Sep 2026 (birth
    # status "Child", adoption types "SIBRA" and "ICA Relative", offered again
    # since 7 Oct): a value a later change retires must keep its record valid.
    birth_status = serializers.CharField(required=False, allow_blank=True)
    type_of_adoption = serializers.CharField(required=False, allow_blank=True)
    # And for Referral Source, which was free text until it became a list.
    referral_source = serializers.CharField(required=False, allow_blank=True, max_length=150)

    termination = serializers.SerializerMethodField()
    terminations = serializers.SerializerMethodField()
    # Computed V2 profile surface: has the pre-assessment been answered, and
    # which instrument titles were used (titles only — copyright policy).
    pre_assessment_status = serializers.SerializerMethodField()
    instruments_used = serializers.SerializerMethodField()
    has_case_referral = serializers.SerializerMethodField()
    # Assigning is asking (children/assignment.py): `psychologist` is who holds
    # the child, and these say who has been asked and who said no. For the ISA
    # and social workers only - a decline's reason is written to the social
    # worker, not to whichever psychologist holds the child.
    pending_assignment = serializers.SerializerMethodField()
    declined_assignment = serializers.SerializerMethodField()
    # The custodian's number, consent and confirmation (children/custodian.py).
    # Typed any way a person writes a number; stored as +639XXXXXXXXX.
    custodian_contact = serializers.CharField(required=False, allow_blank=True, max_length=40)
    custodian_contact_display = serializers.SerializerMethodField()
    custodian_sms_consent_by_name = serializers.SerializerMethodField()
    custodian_contact_verified = serializers.SerializerMethodField()
    custodian_texts = serializers.SerializerMethodField()

    class Meta:
        model = Child
        fields = [
            "id", "first_name", "middle_name", "last_name", "fullname", "birth_date", "date_found",
            "gender", "house_number", "street", "landmark",
            "province", "municipality", "barangay", "address",
            "psgc_province", "psgc_municipality", "psgc_barangay",
            "case_type", "case_category", "custodian_name", "status", "case_status", "assignee_sees_history",
            "custodian_contact", "custodian_contact_display", "custodian_sms_consent",
            "custodian_sms_consent_at", "custodian_sms_consent_by_name",
            "custodian_contact_verified", "custodian_texts",
            "place_of_birth_or_found", "birth_status", "legal_status", "legal_status_date",
            "date_of_admission", "date_of_placement_to_custodian", "type_of_adoption",
            "photo", "referral_source", "referral_reason",
            "education_level", "current_placement", "health_condition", "special_needs", "alias",
            "medical_notes", "recommendation",
            "psychologist", "psychologist_name", "social_worker", "social_worker_name",
            "termination", "terminations",
            "pre_assessment_status", "instruments_used", "has_case_referral",
            "pending_assignment", "declined_assignment",
            "updated_at",
        ]
        # The tracker moves only through the advance-status / terminate actions.
        # fullname is derived (Child.save() composes it from the name parts).
        read_only_fields = ["case_status", "updated_at", "fullname", "custodian_sms_consent_at"]

    def get_social_worker_name(self, obj):
        return display_name(obj.social_worker) or None

    def validate_social_worker(self, value):
        request = self.context.get("request")
        user = getattr(request, "user", None)
        role = getattr(getattr(user, "role", None), "role_name", None)
        unchanged = self.instance is not None and value == self.instance.social_worker
        if unchanged:
            return value
        if role != Role.ADMINISTRATOR:
            # A staff member's own new record is set in perform_create, which
            # overrides whatever arrives here; anything else is a transfer.
            if self.instance is None and role == Role.STAFF:
                return value
            raise serializers.ValidationError(
                "Only an administrator can move a record to another social worker.")
        if value is not None and getattr(value.role, "role_name", None) != Role.STAFF:
            raise serializers.ValidationError("Choose a social worker (SW) account.")
        return value

    def validate_custodian_contact(self, value):
        try:
            return custodian.normalise(value)
        except ValueError as exc:
            raise serializers.ValidationError(str(exc))

    def get_custodian_contact_display(self, obj):
        return phone_as_typed(obj.custodian_contact) or None

    def get_custodian_sms_consent_by_name(self, obj):
        return display_name(obj.custodian_sms_consent_by) or None

    def get_custodian_contact_verified(self, obj):
        return obj.custodian_contact_verified_at is not None

    def get_custodian_texts(self, obj):
        return {"on": custodian.texts_allowed(obj), "status": custodian.status_of(obj)}

    def validate_psychologist(self, value):
        """Only an active psychologist can be asked. The field took any user
        id before, a staff account included. The psychologist who holds the
        child, or who has already been asked, passes unchanged even if their
        account has since been archived: the edit form resends it."""
        if value is None:
            return value
        if self.instance is not None:
            if value == self.instance.assigned_psychologist:
                return value
            pending = self._pending(self.instance)
            if pending is not None and pending.psychologist_id == value.pk:
                return value
        if not assignment.is_active_psychologist(value):
            raise serializers.ValidationError("Choose an active psychologist.")
        return value

    def _sees_requests(self):
        request = self.context.get("request")
        role = getattr(getattr(getattr(request, "user", None), "role", None),
                       "role_name", None) if request else None
        return role in (Role.ADMINISTRATOR, Role.STAFF)

    @staticmethod
    def _requests(obj):
        # Newest first (the model's ordering), and prefetched by the list.
        return list(obj.assignment_requests.all())

    def _pending(self, obj):
        return next((r for r in self._requests(obj)
                     if r.status == AssignmentRequest.PENDING), None)

    def get_pending_assignment(self, obj):
        if not self._sees_requests():
            return None
        req = self._pending(obj)
        if req is None:
            return None
        return {"id": req.id, "psychologist": req.psychologist_id,
                "psychologist_name": display_name(req.psychologist) or None,
                "requested_by_name": display_name(req.requested_by) or None,
                "carry_history": req.carry_history,
                "created_at": req.created_at}

    def get_declined_assignment(self, obj):
        """The latest answer, when it was no. Once somebody else is asked or
        the child is assigned, it is history and no longer shown."""
        if not self._sees_requests():
            return None
        reqs = self._requests(obj)
        if not reqs or reqs[0].status != AssignmentRequest.DECLINED:
            return None
        req = reqs[0]
        return {"id": req.id, "psychologist": req.psychologist_id,
                "psychologist_name": display_name(req.psychologist) or None,
                "reason": req.reason, "decided_at": req.decided_at}

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

    def _current_or_unchanged(self, field, value, choices):
        """A value from the current list, or the one the record already holds.
        A record keeps a retired value until somebody changes it; nobody can
        pick one again."""
        if self.instance is not None and value == getattr(self.instance, field):
            return value
        if value and value not in {c[0] for c in choices}:
            raise serializers.ValidationError(f'"{value}" is not a valid choice.')
        return value

    def validate_birth_status(self, value):
        return self._current_or_unchanged("birth_status", value, Child.BIRTH_STATUS_CHOICES)

    def validate_type_of_adoption(self, value):
        return self._current_or_unchanged(
            "type_of_adoption", value, Child.TYPE_OF_ADOPTION_CHOICES)

    def validate_referral_source(self, value):
        return self._current_or_unchanged(
            "referral_source", value, Child.REFERRAL_SOURCE_CHOICES)

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
            for f in ("first_name", "middle_name", "last_name", "fullname"):
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
                self._require(attrs, creating=True, role=role)
        if self.instance is not None and not self._is_legacy_record():
            self._require(attrs, creating=False, role=role)
        self._check_case(attrs)
        self._check_age(attrs)
        self._check_dates(attrs)
        self._check_legal_status_date(attrs)
        self._check_address(attrs)
        self._check_health(attrs)
        refused = custodian.apply(attrs, self.instance, getattr(request, "user", None), role)
        if refused:
            raise serializers.ValidationError(refused)
        return attrs

    # --- The Add Record rules (children/intake.py) ---------------------------

    def _after(self, attrs, field):
        """What `field` will hold once this request is saved."""
        if field in attrs:
            return attrs[field]
        return getattr(self.instance, field, None) if self.instance is not None else None

    def _is_legacy_record(self):
        # Created through the fullname-only door, which never asked for any of
        # this. Holding it to the full intake on its next edit would lock it.
        return not (self.instance.first_name or self.instance.last_name)

    def _require(self, attrs, creating, role=None):
        """Every question the case asks has an answer.

        On create, all of them. On an edit, an answer cannot be taken away -
        but a record from before the rule is not refused for the blanks it
        already had, or nothing about it could be corrected. The questions the
        case type asks are the exception: changing the case type (or the type
        of adoption) asks them again, so they are answered again. The custodian
        is never asked of a psychologist, who cannot record one
        (children/custodian.py): a case-type change on a record with none
        would otherwise be unsavable for them."""
        blank = lambda v: not str(v or "").strip()  # noqa: E731
        case_type = self._after(attrs, "case_type")
        adoption = self._after(attrs, "type_of_adoption")
        health = self._after(attrs, "health_condition")
        reasked = not creating and any(
            f in attrs and attrs[f] != getattr(self.instance, f)
            for f in ("case_type", "type_of_adoption"))
        missing = {}
        for f in intake.required_fields(case_type, adoption, health):
            if f == "custodian_name" and role == Role.PSYCHOLOGIST:
                continue
            if not blank(self._after(attrs, f)):
                continue
            # What the special needs are is never an older blank: no record
            # held a health condition before this one was asked.
            if (creating or not blank(getattr(self.instance, f))
                    or (reasked and f in intake.DYNAMIC) or f == "special_needs"):
                missing[f] = "This field is required."
        if missing:
            raise serializers.ValidationError(missing)

    def _check_health(self, attrs):
        """"With special needs" says what they are; any other answer has none,
        so whatever was typed beside it is dropped rather than kept against a
        child who is healthy. Only when the request touches either answer."""
        if "health_condition" not in attrs and "special_needs" not in attrs:
            return
        if self._after(attrs, "health_condition") == intake.SPECIAL_NEEDS:
            if not str(self._after(attrs, "special_needs") or "").strip():
                raise serializers.ValidationError({"special_needs": "This field is required."})
        else:
            attrs["special_needs"] = ""

    def _check_case(self, attrs):
        """The category has to be one this track offers - checked when either
        of the two is being set, so a record from before the pairing rule is
        not refused on an unrelated edit."""
        if self.instance is not None and not any(
                f in attrs and attrs[f] != getattr(self.instance, f)
                for f in ("case_category", "case_type")):
            return
        case_type = self._after(attrs, "case_type")
        category = self._after(attrs, "case_category")
        offered = intake.CATEGORY_OPTIONS.get(case_type)
        if offered and category in intake.ALL_CATEGORIES and category not in offered:
            raise serializers.ValidationError(
                {"case_category": f"{category} is not a category for {case_type} cases."})

    _ADDRESS_CODES = ("psgc_province", "psgc_municipality", "psgc_barangay")

    def _check_address(self, attrs):
        """The municipality is in the province and the barangay in the
        municipality, and each name is the one its code carries. A slow list in
        the form once offered Ilocos Norte's municipalities under Ilocos Sur,
        and Ilocos Sur / Adams / Alibago - three provinces - saved (29 Sep
        2026). Checked only when a code is being set, so an address typed
        before the lists existed, or saved before this check, is not refused
        on an unrelated edit."""
        if not any(f in attrs and (attrs[f] or "") != (getattr(self.instance, f, "") or "")
                   for f in self._ADDRESS_CODES):
            return
        from locations.models import Barangay, Municipality, Province
        code = {f: (self._after(attrs, f) or "") for f in self._ADDRESS_CODES}
        province = municipality = barangay = None
        if code["psgc_province"]:
            province = Province.objects.filter(psgc_code=code["psgc_province"]).first()
            if province is None:
                raise serializers.ValidationError({"province": "Pick the province from the list."})
        if code["psgc_municipality"]:
            municipality = Municipality.objects.filter(psgc_code=code["psgc_municipality"]).first()
            if municipality is None:
                raise serializers.ValidationError(
                    {"municipality": "Pick the municipality from the list."})
            if province is None or municipality.province_id != province.pk:
                raise serializers.ValidationError({"municipality": (
                    f"{municipality.name} is not in {province.name if province else 'the province picked'}. "
                    "Pick the municipality again.")})
        if code["psgc_barangay"]:
            barangay = Barangay.objects.filter(psgc_code=code["psgc_barangay"]).first()
            if barangay is None:
                raise serializers.ValidationError({"barangay": "Pick the barangay from the list."})
            if municipality is None or barangay.municipality_id != municipality.pk:
                raise serializers.ValidationError({"barangay": (
                    f"{barangay.name} is not in {municipality.name if municipality else 'the municipality picked'}. "
                    "Pick the barangay again.")})
        for field, place in (("province", province), ("municipality", municipality),
                             ("barangay", barangay)):
            if place is not None:
                attrs[field] = place.name

    def _check_age(self, attrs):
        """The age rule (children/intake.py age_range): 5-17, or 18 and over
        for an Adult adoption. Judged where the birth date is being set, and
        again where the case type or type of adoption changes, since that
        changes the rule - the edit form resends an unchanged birth date, and
        a record whose child has since turned 18 must stay editable."""
        born = self._after(attrs, "birth_date")
        if born is None:
            return
        case_type = self._after(attrs, "case_type")
        adoption = self._after(attrs, "type_of_adoption")
        if self.instance is not None:
            moved = any(f in attrs and attrs[f] != getattr(self.instance, f)
                        for f in ("birth_date", "case_type", "type_of_adoption"))
            if not moved:
                return
        today = timezone.localdate()
        age = today.year - born.year - ((today.month, today.day) < (born.month, born.day))
        low, high = intake.age_range(case_type, adoption)
        if age < low or (high is not None and age > high):
            raise serializers.ValidationError(
                {"birth_date": intake.age_refusal(case_type, adoption)})

    def _check_legal_status_date(self, attrs):
        """The date the legal status was issued: optional, never in the future
        or before the birth, and gone when there is no legal status."""
        if not self._after(attrs, "legal_status"):
            if self._after(attrs, "legal_status_date") is not None:
                attrs["legal_status_date"] = None
            return
        value = self._after(attrs, "legal_status_date")
        if value is None:
            return
        born = self._after(attrs, "birth_date")
        born_moved = (self.instance is not None and "birth_date" in attrs
                      and attrs["birth_date"] != self.instance.birth_date)
        unchanged = self.instance is not None and value == self.instance.legal_status_date
        if unchanged and not born_moved:
            return
        if not unchanged and value > timezone.localdate():
            raise serializers.ValidationError(
                {"legal_status_date": "The date issued cannot be in the future."})
        if born and value < born:
            raise serializers.ValidationError(
                {"legal_status_date": "The date issued cannot be before the date of birth."})

    def _check_dates(self, attrs):
        """None of the case dates is in the future or before the child was
        born. Checked only where the date is being set, like the birth date -
        or where the birth date is being moved, which could otherwise put the
        birth after a date already on the record without a word."""
        today = timezone.localdate()
        born = self._after(attrs, "birth_date")
        born_moved = (self.instance is not None and "birth_date" in attrs
                      and attrs["birth_date"] != self.instance.birth_date)
        # Against a moved birth date, only the dates the case shows: an older
        # record can hold the other one, which no screen shows or edits.
        shown = {"date_found", intake.date_field_for(self._after(attrs, "case_type"),
                                                     self._after(attrs, "type_of_adoption"))}
        for f, label in (("date_found", "The date found"),
                         (intake.ADMISSION, "The date of admission"),
                         (intake.PLACEMENT, "The date of placement")):
            value = attrs.get(f)
            if value is None:
                continue
            unchanged = self.instance is not None and value == getattr(self.instance, f)
            if unchanged and not (born_moved and f in shown):
                continue
            if not unchanged and value > today:
                raise serializers.ValidationError({f: f"{label} cannot be in the future."})
            if born and value < born:
                raise serializers.ValidationError(
                    {f: f"{label} cannot be before the date of birth."})

