import json
from pathlib import Path

from django.core.management.base import BaseCommand
from django.db import transaction

from locations.models import Barangay, Municipality, Province

DATA = Path(__file__).resolve().parents[2] / "data" / "psgc_region1.json"

# Rows per INSERT or UPDATE statement. Django lowers it further where the
# database caps the number of parameters in one statement.
BATCH = 500


class Command(BaseCommand):
    help = "Load PSGC provinces, cities/municipalities and barangays from the bundled dataset."

    def add_arguments(self, parser):
        parser.add_argument(
            "--file", default=str(DATA),
            help="Path to a PSGC JSON file (defaults to the bundled Region I subset).")

    @transaction.atomic
    def handle(self, *args, **options):
        payload = json.loads(Path(options["file"]).read_text(encoding="utf-8"))
        self.stdout.write(f"Release: {payload.get('psgc_release', 'unknown')}")

        # Provinces before municipalities before barangays: each level needs
        # the keys of the one above it, including the places added just now.
        prov_ids = self._load(Province, "provinces", payload["provinces"])
        muni_ids = self._load(Municipality, "municipalities", payload["municipalities"],
                              parent="province", parent_ids=prov_ids)
        self._load(Barangay, "barangays", payload["barangays"],
                   parent="municipality", parent_ids=muni_ids)
        self.stdout.write(self.style.SUCCESS("PSGC loaded."))

    def _load(self, model, label, rows, parent=None, parent_ids=None):
        """Bring one level up to the file, writing only what differs.

        This runs on every start-up of the hosted demo, and the database is a
        network round trip away. The first version asked update_or_create about
        each of the 3,394 places - a lookup, an update and two savepoint calls
        apiece, 13,578 statements to learn that nothing had changed. Now each
        table is read once, compared here, and only the missing or different
        rows are written, in batches. A run with nothing to do writes nothing.

        Rows are keyed on the PSGC code, so a newer release renames places in
        situ rather than duplicating them - and a child record pointing at a
        code keeps pointing at the same place. That is why a changed row is
        updated and never replaced: its primary key must not move. Returns
        {psgc_code: primary key} for the level below to point at.
        """
        # A code listed twice takes its last row, as update_or_create would
        # have by running through them in order.
        wanted = {row["code"]: row for row in rows}
        have = {place.psgc_code: place for place in model.objects.order_by()}
        link = f"{parent}_id" if parent else None

        fresh, changed = [], []
        for code, row in wanted.items():
            parent_id = parent_ids[row[parent]] if parent else None
            place = have.get(code)
            if place is None:
                values = {"psgc_code": code, "name": row["name"]}
                if parent:
                    values[link] = parent_id
                fresh.append(model(**values))
            elif place.name != row["name"] or (parent and getattr(place, link) != parent_id):
                place.name = row["name"]
                if parent:
                    setattr(place, link, parent_id)
                changed.append(place)

        model.objects.bulk_create(fresh, batch_size=BATCH)
        model.objects.bulk_update(
            changed, ["name", parent] if parent else ["name"], batch_size=BATCH)
        self.stdout.write(
            f"  {label + ':':<16} {len(wanted):>5} ({len(fresh)} new, {len(changed)} updated)")

        if not fresh:
            return {code: place.pk for code, place in have.items()}
        # Not every database hands back the keys of a bulk insert, so ask.
        return dict(model.objects.order_by().values_list("psgc_code", "pk"))
