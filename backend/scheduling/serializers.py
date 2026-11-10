from django.utils import timezone
from rest_framework import serializers
from rest_framework.exceptions import APIException, PermissionDenied

from accounts.models import Role
from accounts.scoping import role_of
from config.clock import clock
from scheduling import visibility
from scheduling.models import AvailabilityBlock, Appointment, Unavailability


class BookedAhead:
    """`booked_ahead` for many availability windows from ONE query.

    `blocks_complete` says the list already holds every active window of each
    psychologist on it (the list endpoint's own read), so the one-off dates
    that override a weekly window are taken from it; otherwise, as for a
    single window after a save, they are asked for.

    The rule is the one the per-window query used to state: a session of the
    window's psychologist, from now on, not cancelled, starting inside the
    window's hours, and on its date or (for a weekly window) its weekday.
    Times, dates and weekdays are read in the current time zone, as the
    database lookups did.
    """

    def __init__(self, blocks, *, blocks_complete=False):
        ids = {b.psychologist_id for b in blocks}
        self._starts = {}
        for psychologist_id, start in (
                Appointment.objects
                .filter(psychologist_id__in=ids, start__gte=timezone.now())
                .exclude(status=Appointment.CANCELLED)
                .values_list("psychologist_id", "start")):
            self._starts.setdefault(psychologist_id, []).append(timezone.localtime(start))
        # A one-off window replaces the weekly one on its own date, so a
        # session on such a date sits in the override, not in the weekly window.
        today = timezone.localdate()
        if blocks_complete:
            dated = [(b.psychologist_id, b.date) for b in blocks
                     if b.active and b.date is not None and b.date >= today]
        else:
            dated = (AvailabilityBlock.objects
                     .filter(psychologist_id__in=ids, active=True, date__isnull=False,
                             date__gte=today)
                     .values_list("psychologist_id", "date"))
        self._overridden = {}
        for psychologist_id, day in dated:
            self._overridden.setdefault(psychologist_id, set()).add(day)

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
            elif local.date() in self._overridden.get(block.psychologist_id, ()):
                continue
            n += 1
        return n


class DayAlreadyHasWindow(APIException):
    """400 carrying the sentence AND the id of the window to edit instead.

    A ValidationError would turn the id into a string and wrap every value in
    a list, and the screen needs the id as a number to open that window. This
    is not a ValidationError, so serializer.run_validation lets it through
    untouched and the handler renders `detail` as given.
    """
    status_code = 400
    default_detail = "That day already has availability."

    def __init__(self, message, existing_id):
        super().__init__(message)
        self.detail = {"start_time": message, "existing_id": existing_id}


def day_phrase(block):
    """"Mondays" for a weekly window, "16 Nov" for a one-off date."""
    if block.date is not None:
        return f"{block.date.day} {block.date:%b}"
    return f"{dict(AvailabilityBlock.WEEKDAYS).get(block.weekday, '')}s"


def window_phrase(block):
    """"8:00 AM-12:00 PM, 2 slots" - the existing window in words."""
    slots = f"{block.capacity} slot{'' if block.capacity == 1 else 's'}"
    return f"{clock(block.start_time)}-{clock(block.end_time)}, {slots}"


class AvailabilityBlockSerializer(serializers.ModelSerializer):
    psychologist_name = serializers.CharField(
        source="psychologist.fullname", read_only=True, default=None)
    booked_ahead = serializers.SerializerMethodField()
    outside_hours = serializers.SerializerMethodField()

    class Meta:
        model = AvailabilityBlock
        fields = ["id", "psychologist", "psychologist_name", "weekday", "date",
                  "start_time", "end_time", "capacity", "active", "booked_ahead",
                  "outside_hours"]
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

    def get_outside_hours(self, obj):
        """Upcoming sessions on this window's day(s) that start OUTSIDE its
        hours, so saving can say how many now sit beyond the posted time.

        A one-off window replaces the weekly one on its date, and a change to
        availability reports what is booked and changes nothing: those
        sessions stay, as agreed with somebody. Cancelled and past sessions
        are not counted.

        Only the reply to a save carries it: the list (which hands in
        `booked_ahead`) gets null, or it would cost queries per window.
        """
        if self.context.get("booked_ahead") is not None:
            return None
        upcoming = (Appointment.objects
                    .filter(psychologist_id=obj.psychologist_id,
                            start__gte=timezone.now())
                    .exclude(status=Appointment.CANCELLED))
        if obj.date is not None:
            upcoming = upcoming.filter(start__date=obj.date)
        elif obj.weekday is not None:
            upcoming = upcoming.filter(start__week_day=(obj.weekday + 2) % 7 or 7)
            overridden = list(AvailabilityBlock.objects.filter(
                psychologist_id=obj.psychologist_id, active=True,
                date__isnull=False, date__gte=timezone.localdate())
                .values_list("date", flat=True))
            if overridden:
                upcoming = upcoming.exclude(start__date__in=overridden)
        else:
            return 0
        inside = upcoming.filter(start__time__gte=obj.start_time,
                                 start__time__lt=obj.end_time).count()
        return upcoming.count() - inside

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

        # One window a day. A day holds a single window and its slot count is
        # the day's limit; adding availability to a day that has one means
        # EDITING that day. Weekly windows are unique per (psychologist,
        # weekday), one-off windows per (psychologist, date).
        request = self.context.get("request")
        user = getattr(request, "user", None)
        psy = attrs.get("psychologist") or (self.instance.psychologist if self.instance else None)
        if psy is None:
            psy = user
        if user is not None and getattr(user, "pk", None) is not None:
            role = role_of(request)
            # Checked against whose window this ends up on, not only whose it
            # was (the view repeats it for the save): the sentence below
            # describes a window, so it is not given to someone who may not
            # touch that calendar.
            if role != Role.ADMINISTRATOR and (
                    role != Role.PSYCHOLOGIST or psy is None or psy.pk != user.pk):
                if self.instance is None and role == Role.PSYCHOLOGIST:
                    psy = user          # create files it under themselves
                else:
                    raise PermissionDenied("You can only manage your own availability.")
        active = attrs.get("active", self.instance.active if self.instance else True)
        if psy is not None and getattr(psy, "pk", None) is not None and start and end:
            qs = AvailabilityBlock.objects.filter(psychologist=psy, active=True)
            qs = (qs.filter(date=date) if date is not None
                  else qs.filter(date__isnull=True, weekday=weekday))
            if self.instance is not None:
                qs = qs.exclude(pk=self.instance.pk)
            held = self.instance
            same_day = (held is not None and held.active and held.psychologist_id == psy.pk
                        and held.date == date and (date is not None or held.weekday == weekday))
            if active and not same_day:
                taken = qs.order_by("id").first()
                if taken is not None:
                    raise DayAlreadyHasWindow(
                        f"{day_phrase(taken)} already have availability "
                        f"({window_phrase(taken)}). Edit that day instead."
                        if taken.date is None else
                        f"{day_phrase(taken)} already has availability "
                        f"({window_phrase(taken)}). Edit that day instead.",
                        taken.pk)
            elif active:
                # A day that already held two windows before this rule keeps
                # working and stays editable, but may not be stretched over
                # its twin: capacity would double-count across the pair.
                clash = qs.filter(start_time__lt=end, end_time__gt=start).first()
                if clash:
                    raise serializers.ValidationError({
                        "start_time": f"Overlaps an existing block ({day_phrase(clash)} "
                                      f"{clock(clash.start_time)}\u2013{clock(clash.end_time)}) "
                                      "\u2014 edit that block instead."})
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
