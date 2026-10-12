from django import forms
from django.contrib import admin

from assistant.models import AssistantJob, AssistantSetting
from assistant.runtime import record_runtime_change, runtime_url_refusal


class AssistantSettingForm(forms.ModelForm):
    """The same rule as the Settings screen: an address being changed has to be
    this machine. An unchanged one is left alone, so the form can still be used
    to switch the assistant off."""

    class Meta:
        model = AssistantSetting
        fields = "__all__"

    def clean_ollama_url(self):
        url = self.cleaned_data["ollama_url"]
        if "ollama_url" in self.changed_data:
            refusal = runtime_url_refusal(url)
            if refusal:
                raise forms.ValidationError(refusal)
        return url


@admin.register(AssistantSetting)
class AssistantSettingAdmin(admin.ModelAdmin):
    form = AssistantSettingForm
    list_display = ("enabled", "model_name", "ollama_url", "updated_at")

    def save_model(self, request, obj, form, change):
        before = AssistantSetting.objects.filter(pk=obj.pk).values_list(
            "ollama_url", "model_name").first() or (None, None)
        super().save_model(request, obj, form, change)
        record_runtime_change(request.user, *before, obj.ollama_url, obj.model_name)


@admin.register(AssistantJob)
class AssistantJobAdmin(admin.ModelAdmin):
    """The audit trail, to look at and never to change or take apart.

    Not the text. A row carries what the model wrote (`output_text`: a drafted
    brief, a report summary, a tool call) and, on a chat, the question a person
    typed (`input_ref`), and the Django admin is a second, wider door onto them:
    every superuser holds it, and the seeded ISA is one. What the ISA may know
    about the assistant's use is who, when, which child, what kind and how it
    ended - the access log on the child's page, which never shows text either.
    The reference ("child:12", "report:3") is shown for every job but a chat,
    where it is a question.

    Read-only, in all three verbs: a row edited or deleted here would rewrite
    the access log without leaving a trace.
    """

    list_display = ("created_at", "job_type", "reference", "ok", "outcome", "latency_ms")
    list_filter = ("job_type", "ok", "outcome")
    fields = ("created_at", "job_type", "reference", "created_by", "child", "ok",
              "outcome", "answer", "result_count", "model_used", "latency_ms", "error")
    readonly_fields = fields

    @admin.display(description="Reference")
    def reference(self, obj):
        return "(a typed question - not shown)" if obj.job_type == "chat" else obj.input_ref

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
