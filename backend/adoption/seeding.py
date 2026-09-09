"""One implementation of the two seeders, callable from either side.

The management commands hand these the real models; the data migrations hand
them historical ones out of `apps.get_model`. That is the whole reason the
model classes are arguments rather than imports — a migration must never touch
`adoption.models`, and a developer running the command by hand must not get
different rows from the ones a deploy installs.

Both are idempotent and additive. Neither ever updates or deletes.
"""
from adoption.stage_config import REQUIREMENTS, STAGES


def install_stages(AdoptionStage, RequirementTemplate):
    """Install the eight stages and their requirement templates.

    `get_or_create`, never update: a day target an office has corrected
    survives. Re-running only fills gaps — a stage that was deleted comes back,
    a stage that was edited is left as the office edited it.

    Returns (stages_added, requirements_added).
    """
    stages_added = 0
    requirements_added = 0

    for number, name, owner_role, target_days in STAGES:
        stage, made = AdoptionStage.objects.get_or_create(
            number=number,
            defaults={"name": name, "owner_role": owner_role, "target_days": target_days},
        )
        stages_added += int(made)

        for position, (code, label, external) in enumerate(REQUIREMENTS.get(number, [])):
            _, made_req = RequirementTemplate.objects.get_or_create(
                stage=stage, code=code,
                defaults={"label": label, "owned_externally": external,
                          "position": position},
            )
            requirements_added += int(made_req)

    return stages_added, requirements_added


def backfill_handoffs(Handoff, Child, PreAssessment, *, completed_status, active_status):
    """Create handoff rows for assessments completed before this app existed.

    The handoff row is written by a post_save signal, so it fires only for
    assessments completed after the adoption app was installed. Every child
    signed off before that has no row — and the banner built from those rows is
    the only thing that tells staff somebody is waiting. Without this the
    module would go live blind to its entire existing backlog.

    The status values are arguments because a historical model carries no class
    constants; see the migration for the frozen copies.

    Returns the number of rows created.
    """
    # Newest completion per child is the one that released them; earlier ones
    # are history. Ordering ascending and letting the loop overwrite leaves the
    # newest in `latest`.
    latest = {}
    for pa in (PreAssessment.objects
               .filter(status=completed_status, child__status=active_status)
               .select_related("child", "psychologist")
               .order_by("completed_at", "id")):
        latest[pa.child_id] = pa

    made = 0
    for child_id, pa in latest.items():
        if Handoff.objects.filter(child_id=child_id).exists():
            continue
        Handoff.objects.create(
            child_id=child_id, pre_assessment=pa, released_by=pa.psychologist,
            # The date they were actually released, not the date this happened
            # to run — otherwise every backlogged child looks like they were
            # released today.
            released_at=pa.completed_at or pa.updated_at,
        )
        made += 1

    return made
