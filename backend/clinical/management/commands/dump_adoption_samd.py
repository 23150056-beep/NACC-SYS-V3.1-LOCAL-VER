"""Export the adoption and SAMD tables before the teardown migration drops them.

`children/0019_drop_adoption_samd` is deliberately irreversible - its own
docstring says so - and `backend/entrypoint.sh` runs `migrate` on every deploy.
So the first deploy that carries that migration to a database DROPs ten tables
on it, and on the Neon production branch that is agency data.

The dump CLAUDE.md cites (312 rows, 17 Sep 2026) was taken during local-only
work and is not in this repository. This command exists so the same dump can be
taken from whichever database is actually about to be migrated, rather than
trusting a file nobody can find.

Read-only. It opens its own connection, issues nothing but SELECT, and never
touches the Django ORM - the models are gone, so there is nothing to import.

    # against a hosted database, explicitly
    python manage.py dump_adoption_samd --database-url "postgresql://..." -o backup.json

    # against whatever DATABASE_URL/settings already point at
    python manage.py dump_adoption_samd -o backup.json

Tables that do not exist are recorded as absent rather than raising: a database
built after the removal never had them, and that is a fine answer to get.
"""
import json
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from django.core.management.base import BaseCommand, CommandError
from django.db import connections

# The models' explicit db_table names, child tables first - the same order and
# the same list the teardown migration uses. Kept in this order so a reader can
# diff the two by eye.
ADOPTION_TABLES = [
    "tbl_adoption_clock",
    "tbl_adoption_stage_event",
    "tbl_adoption_requirement",
    "tbl_adoption_case",
    "tbl_adoption_requirement_template",
    "tbl_adoption_handoff",
    "tbl_adoption_pap",
    "tbl_adoption_stage",
]
SAMD_TABLES = ["tbl_samd_response", "tbl_samd_assessment"]
ALL_TABLES = ADOPTION_TABLES + SAMD_TABLES


def _plain(value):
    """JSON cannot hold a datetime, a Decimal or a UUID; a backup must."""
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, (bytes, memoryview)):
        return bytes(value).hex()
    return value


class Command(BaseCommand):
    help = "Export the adoption/SAMD tables to JSON before they are dropped."

    def add_arguments(self, parser):
        parser.add_argument("-o", "--output", required=True,
                            help="Where to write the JSON.")
        parser.add_argument("--database-url", default=None,
                            help="Postgres URL to read instead of the configured "
                                 "database. Use this to dump production from a "
                                 "developer machine.")

    def handle(self, *args, **options):
        url = options["database_url"]
        if url:
            rows, meta = self._via_url(url)
        else:
            rows, meta = self._via_settings()

        payload = {"captured_at": datetime.now().astimezone().isoformat(),
                   "source": meta, "tables": rows}
        with open(options["output"], "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, ensure_ascii=False)

        total = 0
        self.stdout.write(f"source: {meta}")
        for table in ALL_TABLES:
            entry = rows[table]
            if entry is None:
                self.stdout.write(f"  {table:<36} absent")
            else:
                total += len(entry)
                self.stdout.write(f"  {table:<36} {len(entry):>6} rows")
        self.stdout.write(self.style.SUCCESS(
            f"\n{total} rows written to {options['output']}"))
        if total == 0:
            self.stdout.write(
                "Nothing was in those tables, so the teardown migration has "
                "nothing to destroy on this database.")

    # -- the two ways in -------------------------------------------------

    def _via_url(self, url):
        try:
            import psycopg
        except ImportError as exc:  # pragma: no cover - environment problem
            raise CommandError("psycopg is needed for --database-url") from exc
        with psycopg.connect(url, connect_timeout=30) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT current_database(), inet_server_addr()::text")
                db, host = cur.fetchone()
                return self._read(cur, _quote=lambda n: f'"{n}"'), f"{db} @ {host or 'neon'}"

    def _via_settings(self):
        conn = connections["default"]
        with conn.cursor() as cur:
            return (self._read(cur, _quote=lambda n: f'"{n}"'),
                    f"{conn.settings_dict.get('ENGINE')} "
                    f"{conn.settings_dict.get('NAME')}")

    def _read(self, cur, _quote):
        out = {}
        for table in ALL_TABLES:
            try:
                cur.execute(f"SELECT * FROM {_quote(table)}")
            except Exception:
                # A missing table is an answer, not a failure - but the
                # transaction is now poisoned on Postgres, so start a new one.
                out[table] = None
                try:
                    cur.execute("ROLLBACK")
                except Exception:
                    pass
                continue
            columns = [c[0] for c in cur.description]
            out[table] = [
                {col: _plain(val) for col, val in zip(columns, row)}
                for row in cur.fetchall()
            ]
        return out
