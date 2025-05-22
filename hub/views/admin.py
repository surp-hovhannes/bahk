from django.contrib.admin.views.decorators import staff_member_required
from django.shortcuts import render, get_object_or_404
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from hub.models import Reading, LLMPrompt
from hub.services.openai_service import generate_context as generate_context_with_openai
from hub.services.anthropic_service import generate_context as generate_context_with_anthropic

@staff_member_required
@require_http_methods(["GET", "POST"])
def compare_reading_prompts(request, reading_id):
    """View for comparing different LLM prompts for a reading."""
    reading = get_object_or_404(Reading, id=reading_id)
    prompts = LLMPrompt.objects.all()
    outputs = []

    if request.method == "POST":
        prompt1_id = request.POST.get("prompt1")
        prompt2_id = request.POST.get("prompt2")

        if prompt1_id and prompt2_id:
            prompt1 = get_object_or_404(LLMPrompt, id=prompt1_id)
            prompt2 = get_object_or_404(LLMPrompt, id=prompt2_id)

            # Generate contexts for both prompts using the appropriate service
            def get_context(prompt):
                if "gpt" in prompt.model:
                    return generate_context_with_openai(reading, prompt)
                elif "claude" in prompt.model:
                    return generate_context_with_anthropic(reading, prompt)
                else:
                    return None

            context1 = get_context(prompt1)
            context2 = get_context(prompt2)

            if context1 and context2:
                outputs = [
                    {"prompt": prompt1, "text": context1},
                    {"prompt": prompt2, "text": context2}
                ]

    context = {
        "reading": reading,
        "prompts": prompts,
        "outputs": outputs,
    }
    return render(request, "admin/compare_reading_prompts.html", context) 