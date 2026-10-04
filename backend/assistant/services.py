"""Client seam for the on-premises model runtime.

get_ai_client() returns a live client when the master switch is on, else a
NullClient. Every caller must handle AIUnavailable — the system is fully
functional without the assistant.

There is deliberately only one provider. A hosted API would mean sending
clinical free text to a processor outside the agency's data-processing
agreements, which is what removed the V2 layer. The seam is kept so adding one
later is an addition rather than a rewrite; adding one is its own decision.
"""
import http.client
import json
import logging
import threading
import time
import urllib.error
import urllib.request

from assistant.models import AssistantJob, AssistantSetting

logger = logging.getLogger(__name__)

DISCLAIMER = ("AI-drafted decision support, not a diagnosis. The licensed "
              "psychologist reviews, edits, and approves all content.")

# On 4 CPU cores, concurrent generations make every request slower rather than
# parallel, and each parallel slot multiplies the KV cache against very little
# free RAM. One generation at a time, always.
_GENERATION_LOCK = threading.Lock()


class AIUnavailable(Exception):
    """Raised whenever a draft cannot be produced. Always surfaces as a 503."""


SWITCHED_OFF = "The assistant is switched off."

# Said when a drafting feature is asked for on a deployment whose model is
# hosted. The drafting features send case notes, whole reports and a child's
# own answers; the chatbot sends only the question.
HOSTED_DRAFTING_REFUSED = (
    "This deployment runs the chatbot on a hosted model, and drafting from "
    "case records is kept to the agency's own machine.")


class NullClient:
    available = False
    model = ""

    def __init__(self, reason=SWITCHED_OFF):
        self.reason = reason

    def generate(self, prompt, system=None):
        raise AIUnavailable(self.reason)

    def choose_tool(self, question, tool_payload, system=None):
        raise AIUnavailable(self.reason)


class OllamaClient:
    available = True

    def __init__(self, base_url, model):
        self.base_url = base_url.rstrip("/")
        self.model = model

    def _post(self, path, payload):
        """One request to the runtime, and one translation of its failures.

        generate() and choose_tool() hit different endpoints but talked to
        them with the same twelve lines and the same six-exception tuple.
        Losing one exception from one copy is a 500 where the user should
        have been told the assistant is unavailable.
        """
        req = urllib.request.Request(
            f"{self.base_url}{path}",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
            method="POST")
        try:
            with urllib.request.urlopen(req, timeout=180) as resp:
                return json.loads(resp.read().decode())
        except (urllib.error.URLError, TimeoutError, OSError,
                json.JSONDecodeError, http.client.HTTPException,
                UnicodeDecodeError) as exc:
            raise AIUnavailable(f"Local AI runtime unreachable: {exc}") from exc

    def generate(self, prompt, system=None):
        # No num_ctx, no temperature, no options block: each distinct option set
        # forces Ollama to evict and reload the model (~5-6s).
        payload = {"model": self.model, "prompt": prompt, "stream": False}
        if system:
            payload["system"] = system
        data = self._post("/api/generate", payload)
        return (data.get("response") or "").strip()

    def choose_tool(self, question, tool_payload, system=None):
        """Ask the model to pick one tool. Returns (name, raw_args).

        A different endpoint from generate(): tool calling needs /api/chat.
        Same rule about options — none are sent, because each distinct set
        makes the runtime evict and reload the model.
        """
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": question})
        payload = {"model": self.model, "messages": messages,
                   "tools": tool_payload, "stream": False}
        data = self._post("/api/chat", payload)

        calls = (data.get("message") or {}).get("tool_calls") or []
        if not calls:
            # Prose instead of a tool call. Not an error — the caller treats it
            # as "nothing I can do", which is a real answer.
            return None, {}
        fn = calls[0].get("function") or {}
        return fn.get("name"), fn.get("arguments") or {}


def hosted_model_configured():
    """True when this deployment has consented to, and configured, a hosted
    model. Credentials alone are not consent; see settings.py."""
    from django.conf import settings

    return bool(settings.ASSISTANT_ALLOW_HOSTED_MODEL
                and settings.ASSISTANT_MODEL_URL
                and settings.ASSISTANT_MODEL_TOKEN
                and settings.ASSISTANT_MODEL_NAME)


def drafting_available():
    """Whether a drafting feature (brief, remark polish, document summary,
    census narrative, the self-report model check) can run on this deployment
    at all.

    get_ai_client() refuses every caller that does not pass allow_hosted=True
    once a hosted model is configured, so this is exactly that condition.
    Deliberately not the administrator's switch: that is runtime state that
    already answers 503 per click, and a brief drafted before it was turned off
    stays readable (LatestBriefView). /assistant/capabilities/ reports this so
    the screens can hide buttons that could only fail.
    """
    return not hosted_model_configured()


def get_ai_client(*, allow_hosted=False):
    """The one place that decides which model answers.

    Order matters. The administrator's off switch outranks everything; then a
    hosted model, but only with an explicit acknowledgement AND complete
    credentials; otherwise the local runtime, which is the default and the only
    one where nothing leaves the machine.

    `allow_hosted` is the caller saying its prompt may leave the machine. Only
    the chatbot says so - its prompt is the typed question, never a record -
    and the probes that test it. Everything else drafts from case records:
    briefs from case notes, summaries from whole reports, polish from a note
    being written, the self-report check from a child's own answers. "Only the
    chatbot runs on the hosted model" was written down three times (CLAUDE.md,
    CLOUD-DEPLOYMENT.md, the deployment plan) and enforced nowhere, so with
    the flag on every one of those went to the hosted provider. Refusing is
    the default so that a new caller has to opt in rather than remember to
    opt out.

    A drafting caller on a hosted deployment is refused, not sent to the local
    runtime: the hosted branch ignores AssistantSetting.ollama_url because that
    field is administrator-editable, so on a public deployment anyone holding
    administrator credentials could otherwise repoint the model at a host they
    control and capture every prompt. The same reasoning covers drafting.
    """
    from django.conf import settings

    cfg = AssistantSetting.load()
    if not cfg.enabled:
        return NullClient()

    if hosted_model_configured():
        if not allow_hosted:
            return NullClient(HOSTED_DRAFTING_REFUSED)
        # Said out loud, every time, so "which model answered this" is never a
        # guess. The token is never logged.
        logger.info("Assistant is using the HOSTED model %s at %s",
                    settings.ASSISTANT_MODEL_NAME, settings.ASSISTANT_MODEL_URL)
        return OpenAICompatibleClient(settings.ASSISTANT_MODEL_URL,
                                      settings.ASSISTANT_MODEL_NAME,
                                      settings.ASSISTANT_MODEL_TOKEN)

    return OllamaClient(cfg.ollama_url, cfg.model_name)


def gate():
    """Return the config when the assistant may run, else raise AIUnavailable.

    One switch, not one per feature: an administrator who needs to stop the
    assistant needs it stopped, and a per-feature matrix was a configuration
    surface nobody used.
    """
    cfg = AssistantSetting.load()
    if not cfg.enabled:
        raise AIUnavailable("The assistant is switched off.")
    return cfg


# Post-processing beats prompting the model about punctuation: prompting is
# advisory, this is certain. Curly quotes render as mojibake in exported PDFs.
_PUNCTUATION = {
    "‘": "'", "’": "'",
    "“": '"', "”": '"',
    "–": "-", "—": "-",
    " ": " ",
}


def _normalize_output(text):
    for bad, good in _PUNCTUATION.items():
        text = text.replace(bad, good)
    return text


def run_job(job_type, prompt, *, system=None, input_ref="", user=None):
    """Run one generation and audit it. Returns (text, AssistantJob).

    Writes an AssistantJob row on failure as well as success, so "it stopped
    working on Tuesday" is answerable from data rather than from memory.
    """
    client = get_ai_client()
    creator = user if getattr(user, "is_authenticated", False) else None
    started = time.monotonic()
    try:
        # Only the generation is serialised; the DB writes below are not.
        with _GENERATION_LOCK:
            raw = client.generate(prompt, system=system)
    except AIUnavailable as exc:
        AssistantJob.objects.create(
            job_type=job_type, input_ref=input_ref, ok=False,
            error=str(exc)[:255], model_used=getattr(client, "model", ""),
            latency_ms=int((time.monotonic() - started) * 1000),
            created_by=creator)
        raise

    text = _normalize_output(raw)
    job = AssistantJob.objects.create(
        job_type=job_type, input_ref=input_ref, output_text=text,
        model_used=client.model, ok=True,
        latency_ms=int((time.monotonic() - started) * 1000),
        created_by=creator)
    return text, job


def services_lock():
    """The generation lock, for callers that talk to the client directly.

    One generation at a time regardless of which endpoint asked: on four CPU
    cores concurrent runs are slower rather than parallel, and each parallel
    slot multiplies the KV cache against very little free RAM.
    """
    return _GENERATION_LOCK


class OpenAICompatibleClient:
    """A model served over /v1/chat/completions with a bearer token.

    Presents the same two methods as OllamaClient, so nothing above this line
    knows which one it is talking to.

    Written for Cloudflare Workers AI and equally valid against local Ollama's
    own /v1 endpoint — this is an interface, not a vendor.

    A spike measured @cf/meta/llama-4-scout-17b-16e-instruct at 33/33 routing
    over three passes, median 0.6s, against the real six-tool schema in both
    English and Tagalog. It also found that @cf/qwen/qwen3-30b-a3b-fp8 accepts
    the tools array and then returns the call as raw <tool_call> text instead
    of the structured field, which reads as "no tool call" here and would turn
    every question into a polite refusal while the service looked healthy. The
    model must return structured tool_calls, and changing it means running the
    spike again rather than trusting a hunch.
    """

    def __init__(self, base_url, model, token):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self._token = token

    def _post(self, body):
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(body).encode(), method="POST",
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {self._token}"})
        try:
            with urllib.request.urlopen(req, timeout=180) as resp:
                return json.loads(resp.read())
        except Exception as exc:                             # noqa: BLE001
            raise AIUnavailable(f"Model host unreachable: {exc}") from exc

    @staticmethod
    def _message(payload):
        choices = payload.get("choices") or []
        return (choices[0].get("message") or {}) if choices else {}

    def generate(self, prompt, system=""):
        return str(self._message(self._post({
            "model": self.model,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": prompt}],
        })).get("content") or "").strip()

    def choose_tool(self, question, tool_payload, system):
        message = self._message(self._post({
            "model": self.model,
            "tools": tool_payload,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": question}],
        }))
        calls = message.get("tool_calls") or []
        if not calls:
            # Prose, or a model that emitted <tool_call> text. Either way this
            # is not a tool call, and manufacturing one from unparsed text
            # would be worse than declining.
            return None, {}
        fn = calls[0].get("function") or {}
        args = fn.get("arguments")
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except ValueError:
                # The validator downstream reports a missing argument; raising
                # here would turn a recoverable turn into an error page.
                args = {}
        return fn.get("name"), (args or {})
