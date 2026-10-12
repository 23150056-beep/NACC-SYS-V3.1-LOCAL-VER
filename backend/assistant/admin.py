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
    list_display = ("created_at", "job_type", "input_ref", "ok", "outcome", "latency_ms")
    list_filter = ("job_type", "ok", "outcome")
    readonly_fields = ("created_at",)
