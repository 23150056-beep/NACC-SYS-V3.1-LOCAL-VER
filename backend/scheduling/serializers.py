from django.utils import timezone
from rest_framework import serializers

from scheduling.models import AvailabilityBlock, Appointment


class AvailabilityBlockSerializer(serializers.ModelSerializer):
    psychologist_name = serializers.CharField(
        source="psychologist.fullname", read_only=True, default=None)

    class Meta:
        model = AvailabilityBlock
        fields = ["id", "psychologist", "psychologist_name", "weekday", "date",
                  "start_time", "end_time", "capacity", "active"]
        extra_kwargs = {"psychologist": {"required": False}}

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
                                  f"{str(clash.start_time)[:5]}–{str(clash.end_time)[:5]}) — "
                                  "edit that block instead."})
        return attrs


class AppointmentSerializer(serializers.ModelSerializer):
    child_name = serializers.CharField(source="child.fullname", read_only=True)
    psychologist_name = serializers.CharField(
        source="psychologist.fullname", read_only=True, default=None)
    booked_by_name = serializers.CharField(
        source="booked_by.fullname", read_only=True, default=None)

    class Meta:
        model = Appointment
        fields = ["id", "child", "child_name", "psychologist", "psychologist_name",
                  "start", "duration_minutes", "purpose", "status",
                  "pre_assessment", "notes", "booked_by", "booked_by_name", "created_at"]
        read_only_fields = ["booked_by", "status"]
        extra_kwargs = {"psychologist": {"required": False}}

    def validate_start(self, value):
        # Checked when MOVING one too, not only when creating it - otherwise a
        # reschedule can drop an appointment into last week. An unchanged start
        # is exempt so that editing the notes on a past session still works.
        if self.instance is not None and value == self.instance.start:
            return value
        if value < timezone.now():
            raise serializers.ValidationError("Cannot book an appointment in the past.")
        return value
