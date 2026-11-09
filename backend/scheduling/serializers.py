from django.utils import timezone
from rest_framework import serializers

from config.clock import clock
from scheduling import visibility
from scheduling.models import AvailabilityBlock, Appointment, Unavailability


class BookedAhead:
    """`booked_ahead` for many availability windows from ONE query.

    The rule is the one the per-window query used to state: a session of the
    window's psychologist, from now on, not cancelled, starting inside the
    window's hours, and on its date or (for a weekly window) its weekday.
    Times, dates and weekdays are read in the current time zone, as the
    database lookups did.
    """

    def __init__(self, blocks):
        ids = {b.psychologist_id for b in blocks}
        self._starts = {}
        for psychologist_id, start in (
                Appointment.objects
                .filter(psychologist_id__in=ids, start__gte=timezone.now())
                .exclude(status=Appointment.CANCELLED)
                .values_list("psychologist_id", "start")):
            self._starts.setdefault(psychologist_id, []).append(timezone.localtime(start))

    def count(self, block):
        n = 0
        for local in self._starts.get(block.psychologist_id, ()):
            if not (block.start_time <= local.time() < block.end_time):
                continue
            if block.date is not None:
                if local.date() != block.date:
                    continue
            elif block.weekday is not None and local.weekday() != block.weekday:
                continue
            n += 1
        return n


class AvailabilityBlockSerializer(serializers.ModelSerializer):
    psychologist_name = serializers.CharField(
        source="psychologist.fullname", read_only=True, default=None)
    booked_ahead = serializers.SerializerMethodField()

    class Meta:
        model = AvailabilityBlock
        fields = ["id", "psychologist", "psychologist_name", "weekday", "date",
                  "start_time", "end_time", "capacity", "active", "booked_ahead"]
        extra_kwargs = {"psychologist": {"required": False}}

    def get_booked_ahead(self, obj):
        """Sessions already booked into this window, from now on.

        Removing a window does not cancel them and must not - they were agreed
        with somebody. But "Remove this availability block?" with nothing else
        on it hid the single fact that decides the answer.

        Past and cancelled sessions are excluded: neither is a reason to
        hesitate, and counting them would train people to ignore the number.

        A list hands in `BookedAhead` (one query for every window on it); a
        single window, as after a save, asks for itself.
        """
        index = self.context.get("booked_ahead")
        if index is None:
            index = BookedAhead([obj])
        return index.count(obj)

    def validate(self, attrs):
        start = attrs.get("start_time") or (self.instance.start_time if self.instance else None)
        end = attrs.get("end_time") or (self.instance.end_time if self.instance else None)
        if start and end and start >= end:
            raise serializers.ValidationError({"end_time": "End must be after start."})
        weekday = attrs.get("weekday", self.instance.weekday if self.instance else None)
        date = attrs.get("date", self.instance.date if self.instance else None)
        if weekday is None and date is None:
            raise serializers.ValidationError(
                {"weekday": "Pick a recurring weekday or a specific date."})

        # One window per psychologist per day-slot: overlapping blocks make the
        # booking capacity check double-count appointments, so reject them.
        # Adjacent windows (e.g. 09:00-12:00 + 12:00-15:00) stay allowed.
        psy = attrs.get("psychologist") or (self.instance.psychologist if self.instance else None)
        if psy is None:
            psy = getattr(self.context.get("request"), "user", None)
        if psy is not None and getattr(psy, "pk", None) is not None and start and end:
            qs = AvailabilityBlock.objects.filter(psychologist=psy, active=True)
            qs = (qs.filter(date=date) if date is not None
                  else qs.filter(date__isnull=True, weekday=weekday))
            if self.instance is not None:
                qs = qs.exclude(pk=self.instance.pk)
            clash = qs.filter(start_time__lt=end, end_time__gt=start).first()
            if clash:
                day = clash.date or f"{dict(AvailabilityBlock.WEEKDAYS).get(clash.weekday, '')}s"
                raise serializers.ValidationError({
                    "start_time": f"Overlaps an existing block ({day} "
                                  f"{clock(clash.start_time)}–{clock(clash.end_time)}) — "
                                  "edit that block instead."})
        return attrs


class AppointmentSerializer(serializers.ModelSerializer):
    # Whether the child's name is shown depends on who is looking - see
    # scheduling/visibility.py. Without a request there is nobody to show it
    # to, so it is left out.
    child_name = serializers.SerializerMethodField()
    case_ref = serializers.SerializerMethodField()
    referred_by_name = serializers.SerializerMethodField()
    name_hidden = serializers.SerializerMethodField()
    psychologist_name = serializers.CharField(
        source="psychologist.fullname", read_only=True, default=None)
    booked_by_name = serializers.CharField(
        source="booked_by.fullname", read_only=True, default=None)

    class Meta:
        model = Appointment
        fields = ["id", "child", "child_name", "case_ref", "referred_by_name", "name_hidden",
                  "psychologist", "psychologist_name",
                  "start", "duration_minutes", "purpose", "status",
                  "pre_assessment", "notes", "booked_by", "booked_by_name", "created_at"]
        read_only_fields = ["booked_by", "status"]
        extra_kwargs = {"psychologist": {"required": False}}

    def _who(self, obj):
        # Computed once per row and shared by the four fields below.
        cache = self.__dict__.setdefault("_who_cache", {})
        if obj.pk not in cache:
            request = self.context.get("request")
            cache[obj.pk] = visibility.who(getattr(request, "user", None), obj.child)
        return cache[obj.pk]

    def to_representation(self, obj):
        data = super().to_representation(obj)
        if self._who(obj)["name_hidden"]:
            # Free text typed by whoever booked it - "bring Ana's school
            # records" - is the child's name by another route. Another social
            # worker's session carries none of it.
            data["notes"] = ""
            data["pre_assessment"] = None
        return data

    def get_child_name(self, obj):
        return self._who(obj)["child_name"]

    def get_case_ref(self, obj):
        return self._who(obj)["case_ref"]

    def get_referred_by_name(self, obj):
        return self._who(obj)["referred_by_name"]

    def get_name_hidden(self, obj):
        return self._who(obj)["name_hidden"]

    def validate_start(self, value):
        # Checked when MOVING one too, not only when creating it - otherwise a
        # reschedule can drop an appointment into last week. An unchanged start
        # is exempt so that editing the notes on a past session still works.
        if self.instance is not None and value == self.instance.start:
            return value
        if value < timezone.now():
            raise serializers.ValidationError("Cannot book an appointment in the past.")
        return value


class UnavailabilitySerializer(serializers.ModelSerializer):
    psychologist_name = serializers.CharField(
        source="psychologist.fullname", read_only=True, default=None)
    booked_during = serializers.SerializerMethodField()

    class Meta:
        model = Unavailability
        fields = ["id", "psychologist", "psychologist_name", "starts_on",
                  "ends_on", "reason", "booked_during", "created_at"]
        # Whose leave it is gets settled in the view: a psychologist's own
        # when they name nobody, "Whose leave is this?" for anybody else.
        # Required here, a psychologist who left it out was told "This field
        # is required" about a field the server fills in itself.
        extra_kwargs = {"psychologist": {"required": False}}

    def get_booked_during(self, obj):
        """Sessions still standing inside these dates.

        Declaring leave never cancels them - they were agreed with somebody -
        so the number is how the screen says what is caught in it, before and
        after. Past and cancelled ones are excluded: neither is something
        anybody has to do anything about.
        """
        return (Appointment.objects
                .filter(psychologist_id=obj.psychologist_id,
                        start__date__gte=obj.starts_on,
                        start__date__lte=obj.ends_on,
                        start__gte=timezone.now())
                .exclude(status=Appointment.CANCELLED)
                .count())

    def validate(self, attrs):
        starts = attrs.get("starts_on") or (self.instance.starts_on if self.instance else None)
        ends = attrs.get("ends_on") or (self.instance.ends_on if self.instance else None)
        if starts and ends and ends < starts:
            raise serializers.ValidationError(
                {"ends_on": "The last day cannot be before the first."})
        return attrs
