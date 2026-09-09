"""The trigger, wired to the assessment rather than to the screen that ends it.

A signal rather than a line in PreAssessmentViewSet.complete because there is
more than one way an assessment reaches Completed — the wizard today, an
import or a correction tomorrow — and a handoff that only happens when one
particular button is pressed is a handoff that will one day not happen. The
banner is the only thing telling staff a child is waiting; it cannot depend on
which code path finished the assessment.
"""
from django.db.models.signals import post_save
from django.dispatch import receiver

from adoption import pipeline
from clinical.models import PreAssessment


@receiver(post_save, sender=PreAssessment, dispatch_uid="adoption_handoff_on_complete")
def release_or_freeze(sender, instance, **kwargs):
    if instance.status == PreAssessment.COMPLETED:
        pipeline.record_handoff(instance)
        # Re-completing lifts a hold that a reopen put on, so a case that was
        # frozen mid-docket carries on where it stopped.
        pipeline.set_hold(instance.child, False)
        return

    # Reopened. Freeze anything already in the pipeline — never delete it.
    if pipeline.AdoptionCase.objects.filter(
            child=instance.child, closed_at__isnull=True).exists():
        pipeline.set_hold(instance.child, True)
