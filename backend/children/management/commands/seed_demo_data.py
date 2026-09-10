"""Generate a believable caseload for local development and demonstration.

Why this exists
---------------
An empty system cannot be developed against or shown. Features that look across
a caseload — care gaps, semantic search, trajectory detection — return nothing
on three test children and look broken rather than empty. And the alternative,
putting real children's records on a laptop, is exactly what the agency's Data
Privacy Act position forbids.

So: invented people, invented words, real structure. Every name, remark and
self-report answer here was written for this file. No child described below
exists.

What it builds
--------------
Three cohorts, because the interesting question is not "is there data" but
"does the data have shape":

* **steady**      — notes and self-reports both stay level or improve.
* **declining**   — both worsen together. The straightforward case.
* **divergent**   — the child's own answers drift toward distress while the
                    case notes stay reassuring. This is the pattern worth
                    detecting, and the one no amount of reading one file at a
                    time will surface.

Notes deliberately code-switch between English, Tagalog and Ilocano, because
notes written in Region I do. A search feature that only works in English is
not a search feature here.
"""
import random
from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from accounts.models import Role, User
from config.demo_guard import refuse_if_not_local
from children.models import Child
from clinical.models import (
    AgencyFormTemplate, ConsentRecord, InstrumentCatalog, OpinionnaireInvite,
    PreAssessment, ProblemEntry, RemarkNote, ResultEntry, TreatmentPlan)
from locations.models import Barangay, Municipality, Province
from scheduling import demo_schedule
from scheduling.models import Appointment

# Invented, but drawn from naming patterns common in the Ilocos region so the
# screens look like the agency's own caseload rather than a Western test fixture.
FIRST_NAMES = [
    "Aiza", "Bartolome", "Carmela", "Danilo", "Elvira", "Ferdinand", "Gliceria",
    "Hector", "Imelda", "Joel", "Kristine", "Lorenzo", "Marisol", "Nestor",
    "Ofelia", "Percival", "Querubin", "Rosalinda", "Salvador", "Teresita",
    "Ulysses", "Violeta", "Wilfredo", "Yolanda", "Zenaida", "Analyn", "Bienvenido",
    "Corazon", "Dominador", "Editha", "Fidel", "Genoveva", "Hilario", "Isabelita",
    "Jocelyn", "Kim", "Lilibeth", "Melchor", "Norberto", "Purificacion",
]
LAST_NAMES = [
    "Agpalza", "Bumanglag", "Cariaga", "Dacanay", "Espiritu", "Foronda",
    "Gaerlan", "Hidalgo", "Ines", "Jimenez", "Kabigting", "Lorenzana",
    "Maranan", "Nalupta", "Oribello", "Pichay", "Quilala", "Ramos", "Sabado",
    "Tabuena", "Ulep", "Valdez", "Wagayen", "Ybanez", "Zamora", "Balbin",
    "Castañeda", "Duque", "Estrada", "Fariñas", "Guillermo", "Hufana",
]

CASE_TYPES = ["Adoption", "Foster Care", "Kinship Care", "Residential Care",
              "Family Tracing & Reunification", "Independent Living"]
CATEGORIES = ["Surrendered", "Abandoned", "Dependent", "Neglected",
              "Without Known Parents", "Orphan"]

# Clinical shorthand, the way a busy psychologist actually writes it — short,
# abbreviated, and switching language mid-sentence.
REMARKS = {
    "steady": [
        "Settling in well. Nakikisalamuha na sa ibang bata during recreation.",
        "Attended session. Bright affect, talkative about school.",
        "Reports sleeping better. No somatic complaints this week.",
        "Napintas ti riknana today — engaged throughout the session.",
        "Foster placement holding. Custodian reports no difficulties.",
        "Completed the drawing task without prompting. Good concentration.",
        "Asked about visiting her sibling. Discussed with social worker.",
        "Participated in group activity. Initiated conversation twice.",
    ],
    "declining": [
        "Quieter than usual. Short answers, minimal eye contact.",
        "Skipped recreation twice this week. Says napudot, but no fever.",
        "Custodian reports difficulty sleeping. Waking around 2am.",
        "Withdrawn during session. Declined the activity, sat apart.",
        "Awan ti ganas nga agsao. Did not respond to open questions.",
        "Appetite down per house parent. Lost interest in usual games.",
        "Tearful when the visit schedule was mentioned. Ended session early.",
        "Refused to join the group today. Stayed in the room.",
    ],
    # The point of the exercise: the record stays reassuring while the child's
    # own answers do not.
    "divergent_notes": [
        "Doing fine. No concerns raised by the house parent.",
        "Attended as scheduled. Cooperative throughout.",
        "Adjusting well to the placement. Nothing to note.",
        "Routine session. Nakangiti naman, participative.",
        "No behavioural issues reported this period.",
        "Progressing as expected. Continue current plan.",
    ],
}

# The child's own words, on the agency's self-report form.
SELF_REPORT = {
    "good": [
        "Masaya naman ako dito. May kaibigan na ako.",
        "Okay lang. I like the food and my bed.",
        "Naimbag met. I can sleep at night now.",
        "I feel safe. Ang bait ng nag-aalaga sa akin.",
    ],
    "mixed": [
        "Minsan okay, minsan hindi. Depende sa araw.",
        "I miss my sister. But the people here are kind.",
        "Adda met bassit nga problema but I don't want to say.",
        "Sometimes I cannot sleep. Naiisip ko yung bahay namin.",
    ],
    "distressed": [
        "Gusto ko na umuwi. Lagi akong umiiyak sa gabi.",
        "Nobody listens to me here. Wala akong makausap.",
        "Mabutbuteng. I am scared but I don't tell them.",
        "I feel alone. Ayaw ko na dito, gusto ko na lang matulog.",
        "Hindi ko masabi kasi baka magalit sila. Masakit ang dibdib ko.",
    ],
}

PROBLEMS = [
    ("Separation anxiety", "Emotional"),
    ("Sleep disturbance", "Physical"),
    ("Withdrawal from peers", "Social"),
    ("School attendance difficulty", "Educational"),
    ("Difficulty expressing emotion", "Emotional"),
    ("Adjustment to placement", "Social"),
]
CLASSIFICATIONS = ["Standard Adjustment", "Moderate Concern", "High Indicator"]


class Command(BaseCommand):
    help = "Create an invented caseload for local development and demos."

    def add_arguments(self, parser):
        parser.add_argument("--children", type=int, default=40,
                            help="How many children to invent (default 40).")
        parser.add_argument("--months", type=int, default=6,
                            help="How far back the history runs (default 6).")
        parser.add_argument("--seed", type=int, default=20260824,
                            help="RNG seed. The same seed gives the same caseload, "
                                 "so a demo can be reproduced exactly.")
        parser.add_argument("--force", action="store_true",
                            help="Proceed even though the database already has children.")
        parser.add_argument("--purge", action="store_true",
                            help="Delete ALL children and clinical records first. "
                                 "Local databases only.")

    def _refuse_if_not_local(self):
        """Two independent guards, because one of them will eventually be wrong.

        This command invents children and writes them to whatever database it is
        pointed at. Running it against the agency's real one would mix fictional
        records into real case files — not a data loss, something worse: a file
        that cannot be trusted.

        The checks live in config.demo_guard because more than one command
        invents data now, and a guard kept in two copies protects whichever
        copy somebody remembered to update.
        """
        refuse_if_not_local()

    def handle(self, *args, **options):
        # Checked before anything opens a connection. This used to sit inside a
        # transaction.atomic-decorated handle(), which meant Django connected to
        # the database first — so pointing this at a hosted one would hang on
        # the connection instead of refusing outright, and the guard could not
        # do the one thing it exists for.
        self._refuse_if_not_local()
        self._seed(options)

    @transaction.atomic
    def _seed(self, options):
        rng = random.Random(options["seed"])
        today = timezone.localdate()

        if options["purge"]:
            self.stdout.write(self.style.WARNING("Purging existing children and clinical records..."))
            Child.objects.all().delete()   # cascades to the clinical tables
        elif Child.objects.exists() and not options["force"]:
            raise CommandError(
                f"The database already has {Child.objects.count()} children. "
                "Use --force to add to them, or --purge to start clean.")

        if not Province.objects.exists():
            raise CommandError("No PSGC addresses loaded. Run: manage.py seed_psgc")

        psychologists = self._ensure_staff(rng)
        # A caseload nobody can be booked with is not a caseload. Without this
        # every psychologist here carries children and has no bookable window,
        # so the one thing the calendar exists for fails for all forty.
        demo_schedule.install_availability(psychologists)
        template = self._ensure_self_report_template()
        instruments = self._ensure_instruments(psychologists)
        places = self._addresses(rng)

        made = self._build_children(rng, options, today, psychologists, template,
                                    instruments, places)

        # The appointments above are placed at rough offsets from today. This
        # puts every one of them on a weekday, inside clinic hours, on a slot
        # its psychologist is actually free for - so the seeded calendar is one
        # the booking endpoint would have accepted. Demo data the real rules
        # would reject is a second system sharing a database.
        moved, _ = demo_schedule.realign_appointments()

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(
            f"Built {made['children']} children across {len(psychologists)} psychologists."))
        self.stdout.write(f"  {moved} appointments placed in clinic hours")
        for label in ("steady", "declining", "divergent"):
            self.stdout.write(f"  {label:<12} {made[label]:>3}")
        self.stdout.write(
            f"  remarks {made['remarks']}, self-reports {made['reports']}, "
            f"appointments {made['appts']}, problems {made['problems']}")
        self.stdout.write("")
        self.stdout.write("Every person here is invented. Seed "
                          f"{options['seed']} reproduces this exact caseload.")

    # ---------------------------------------------------------------- helpers

    def _ensure_staff(self, rng):
        psych_role, _ = Role.objects.get_or_create(role_name=Role.PSYCHOLOGIST)
        staff_role, _ = Role.objects.get_or_create(role_name=Role.STAFF)
        people = [
            ("Marivic", "Bulan", "m.bulan@racco1.gov.ph", psych_role),
            ("Rogelio", "Tolentino", "r.tolentino@racco1.gov.ph", psych_role),
            ("Anabelle", "Suguitan", "a.suguitan@racco1.gov.ph", psych_role),
            ("Editha", "Pascua", "e.pascua@racco1.gov.ph", staff_role),
        ]
        made = []
        for first, last, email, role in people:
            user, created = User.objects.get_or_create(
                email=email,
                defaults={"username": email, "first_name": first, "last_name": last,
                          "role": role, "status": User.ACTIVE})
            if created:
                user.set_password("demo1234")
                user.must_change_password = False
                user.save()
            if role == psych_role:
                made.append(user)
        return made

    def _ensure_self_report_template(self):
        tpl = AgencyFormTemplate.objects.filter(
            form_type=AgencyFormTemplate.SELF_REPORT_GOV).first()
        if tpl:
            return tpl
        return AgencyFormTemplate.objects.create(
            form_type=AgencyFormTemplate.SELF_REPORT_GOV,
            title="Child Self-Report — How I Am Doing",
            body="Answer in your own words. There are no wrong answers.",
            fields=[
                {"label": "How are you feeling this week?", "field_type": "textarea"},
                {"label": "Is there anything worrying you?", "field_type": "textarea"},
                {"label": "Who do you talk to when you are sad?", "field_type": "text"},
            ],
            active=True)

    def _ensure_instruments(self, psychologists):
        titles = ["Child Behaviour Checklist", "Draw-A-Person Test",
                  "Sentence Completion for Children"]
        out = []
        for title in titles:
            obj, _ = InstrumentCatalog.objects.get_or_create(
                title=title, defaults={"owner": psychologists[0], "audience": "child"})
            out.append(obj)
        return out

    def _addresses(self, rng):
        """Real Region I places, so the address picker and any location report
        have something truthful to work with."""
        out = []
        for muni in Municipality.objects.select_related("province").all():
            brgys = list(Barangay.objects.filter(municipality=muni)[:12])
            if brgys:
                out.append((muni.province, muni, brgys))
        rng.shuffle(out)
        return out

    def _build_children(self, rng, options, today, psychologists, template,
                        instruments, places):
        n = options["children"]
        months = options["months"]
        counts = {"children": 0, "steady": 0, "declining": 0, "divergent": 0,
                  "remarks": 0, "reports": 0, "appts": 0, "problems": 0}

        for i in range(n):
            # 55% steady, 25% declining, 20% divergent — enough of each to see,
            # weighted so "most children are doing alright" stays true.
            roll = rng.random()
            cohort = "steady" if roll < 0.55 else ("declining" if roll < 0.80 else "divergent")
            counts[cohort] += 1

            first = rng.choice(FIRST_NAMES)
            last = rng.choice(LAST_NAMES)
            province, muni, brgys = places[i % len(places)]
            brgy = rng.choice(brgys)
            case_type = rng.choice(CASE_TYPES)
            psych = psychologists[i % len(psychologists)]
            intake = today - timedelta(days=rng.randint(30, months * 30))

            child = Child.objects.create(
                first_name=first, last_name=last, fullname=f"{first} {last}",
                birth_date=today - timedelta(days=rng.randint(5, 17) * 365),
                gender=rng.choice(["Male", "Female"]),
                province=province.name, municipality=muni.name, barangay=brgy.name,
                psgc_province=province.psgc_code, psgc_municipality=muni.psgc_code,
                psgc_barangay=brgy.psgc_code,
                case_type=case_type,
                case_category=rng.choice(CATEGORIES),
                surrendered_by=(rng.choice(["Social Worker", "Police", "Relatives"])
                                if case_type in ("Adoption", "Foster Care", "Kinship Care",
                                                 "Family Tracing & Reunification") else ""),
                type_of_adoption=(rng.choice(["Domestic", "Relative", "Stepparent"])
                                  if case_type == "Adoption" else ""),
                birth_status=rng.choice(["Marital", "Non-Marital", "N/A"]),
                date_of_admission=intake,
                assigned_psychologist=psych,
                case_status=rng.choice(["pre_assessment", "counseling"]),
            )
            counts["children"] += 1

            ConsentRecord.objects.create(
                child=child, signer_name=f"{rng.choice(FIRST_NAMES)} {last}",
                signer_relationship=rng.choice(["Guardian", "Custodian", "Social Worker"]),
                date=intake + timedelta(days=2),
                status=ConsentRecord.SIGNED, recorded_by=psych)

            pa = PreAssessment.objects.create(
                child=child, psychologist=psych, date=intake + timedelta(days=5),
                status="completed", completed_at=timezone.now())
            pa.instruments.add(rng.choice(instruments))

            for desc, cat in rng.sample(PROBLEMS, rng.randint(1, 3)):
                ProblemEntry.objects.create(
                    child=child, description=desc, category=cat,
                    identified_on=intake + timedelta(days=rng.randint(5, 20)),
                    resolved=(cohort == "steady" and rng.random() < 0.4),
                    logged_by=psych)
                counts["problems"] += 1

            ResultEntry.objects.create(
                child=child, pre_assessment=pa, instrument=rng.choice(instruments),
                summary="Findings recorded from the paper administration.",
                classification=("High Indicator" if cohort == "declining"
                                else rng.choice(CLASSIFICATIONS)),
                date=intake + timedelta(days=8), entered_by=psych)

            TreatmentPlan.objects.create(
                child=child, author=psych,
                objectives="Build trust, stabilise routine, support peer contact.",
                interventions="Weekly play-based sessions; coordinate with house parent.",
                review_date=today + timedelta(days=rng.randint(10, 60)))

            counts["remarks"] += self._history(rng, child, psych, cohort, intake, today)
            counts["reports"] += self._self_reports(rng, child, psych, cohort,
                                                    template, intake, today)
            counts["appts"] += self._appointments(rng, child, psych, today)

        return counts

    def _history(self, rng, child, psych, cohort, intake, today):
        """Remarks across the whole period. The declining cohort crosses over
        partway through; the divergent one never does — that is the point."""
        made = 0
        day = intake + timedelta(days=10)
        total_days = max((today - day).days, 1)
        while day < today:
            if cohort == "steady":
                pool = REMARKS["steady"]
            elif cohort == "declining":
                progress = (day - intake).days / total_days
                pool = REMARKS["declining"] if progress > 0.45 else REMARKS["steady"]
            else:
                pool = REMARKS["divergent_notes"]
            RemarkNote.objects.create(
                child=child, author=psych, date=day, text=rng.choice(pool))
            made += 1
            day += timedelta(days=rng.randint(9, 18))
        return made

    def _self_reports(self, rng, child, psych, cohort, template, intake, today):
        """The child's own answers, on the same timeline. For the divergent
        cohort these slide toward distress while the notes above do not."""
        made = 0
        day = intake + timedelta(days=21)
        total_days = max((today - day).days, 1)
        while day < today:
            progress = (day - intake).days / total_days
            if cohort == "steady":
                mood = "good" if rng.random() < 0.75 else "mixed"
            elif cohort == "declining":
                mood = "distressed" if progress > 0.5 else "mixed"
            else:
                mood = ("distressed" if progress > 0.55
                        else ("mixed" if progress > 0.25 else "good"))
            answered = timezone.make_aware(
                timezone.datetime.combine(day, timezone.datetime.min.time()))
            OpinionnaireInvite.objects.create(
                child=child, template=template, created_by=psych,
                status=OpinionnaireInvite.SUBMITTED,
                answers={
                    "How are you feeling this week?": rng.choice(SELF_REPORT[mood]),
                    "Is there anything worrying you?": rng.choice(SELF_REPORT[mood]),
                    "Who do you talk to when you are sad?":
                        rng.choice(["My sister", "Nobody", "Ate sa bahay", "Ako lang"]),
                },
                expires_at=answered + timedelta(days=14), submitted_at=answered)
            made += 1
            day += timedelta(days=rng.randint(25, 40))
        return made

    def _appointments(self, rng, child, psych, today):
        made = 0
        for offset in (-45, -30, -15, 7, 21):
            start = timezone.now() + timedelta(days=offset, hours=rng.randint(-3, 3))
            status = (Appointment.COMPLETED if offset < 0 else Appointment.SCHEDULED)
            if offset < 0 and rng.random() < 0.12:
                status = Appointment.NO_SHOW
            Appointment.objects.create(
                child=child, psychologist=psych, start=start,
                duration_minutes=60, status=status, booked_by=psych,
                purpose=rng.choice([Appointment.SESSION, Appointment.FOLLOW_UP]))
            made += 1
        return made
