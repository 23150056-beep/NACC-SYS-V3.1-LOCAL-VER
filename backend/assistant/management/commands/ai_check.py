"""Smoke-test the local model runtime.

Prints what is configured, whether it answers, and how long it took. Exits 1
when a draft could not be produced, so it is usable from a script.
"""
import time

from django.core.management.base import BaseCommand

from assistant.models import AssistantSetting
from assistant.services import AIUnavailable, OllamaClient, get_ai_client


class Command(BaseCommand):
    help = "Check that the local model runtime is reachable and answering."

    def handle(self, *args, **options):
        cfg = AssistantSetting.load()
        if not cfg.enabled:
            self.stdout.write(f"URL:   {cfg.ollama_url}")
            self.stdout.write(f"Model: {cfg.model_name}")
            self.stdout.write("Result: the assistant is switched off.")
            raise SystemExit(1)

        # Report the client actually built, not the settings row. Those two
        # disagree the moment a hosted model is configured: this printed
        # "qwen2.5:3b-instruct / localhost:11434" while timing a Cloudflare
        # model, which is the wrong answer from the one command whose whole
        # job is saying what the assistant is talking to.
        client = get_ai_client(allow_hosted=True)
        hosted = not isinstance(client, OllamaClient)
        self.stdout.write(f"URL:   {client.base_url}")
        self.stdout.write(f"Model: {client.model}")
        self.stdout.write(f"Where: {'HOSTED' if hosted else 'local runtime'}")
        if hosted:
            # What answers the chatbot is not what drafts: that stays local,
            # so on this deployment it does not run at all.
            self.stdout.write("Drafting: off - briefs, summaries, polish and the "
                              "self-report check never use a hosted model.")

        started = time.monotonic()
        try:
            client.generate("Reply with the single word: OK.")
        except AIUnavailable as exc:
            self.stdout.write(f"Result: {exc}")
            raise SystemExit(1)

        elapsed = int((time.monotonic() - started) * 1000)
        self.stdout.write(f"Result: reachable, answered in {elapsed} ms.")
