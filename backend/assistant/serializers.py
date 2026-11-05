from rest_framework import serializers

from assistant.models import AssistantSetting
from assistant.runtime import runtime_url_refusal


class AssistantSettingSerializer(serializers.ModelSerializer):
    class Meta:
        model = AssistantSetting
        fields = ["enabled", "ollama_url", "model_name", "updated_at"]
        read_only_fields = ["updated_at"]

    def validate_ollama_url(self, value):
        # Only an address being CHANGED is judged. The Settings screen sends
        # the address with every save, and an administrator switching the
        # assistant off must not be refused over one saved before this rule;
        # get_ai_client() will not use such an address in any case.
        if self.instance is not None and value == self.instance.ollama_url:
            return value
        refusal = runtime_url_refusal(value)
        if refusal:
            raise serializers.ValidationError(refusal)
        return value
