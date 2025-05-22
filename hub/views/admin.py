from django.contrib.admin.views.decorators import staff_member_required
from django.shortcuts import render, get_object_or_404
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from hub.models import Reading, LLMPrompt
from hub.services.llm_service import get_llm_service

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
            try:
                service1 = get_llm_service(prompt1.model)
                service2 = get_llm_service(prompt2.model)
                
                context1 = service1.generate_context(reading, prompt1)
                context2 = service2.generate_context(reading, prompt2)

                if context1 and context2:
                    outputs = [
                        {"prompt": prompt1, "text": context1},
                        {"prompt": prompt2, "text": context2}
                    ]
            except ValueError as e:
                # Handle unsupported model error
                outputs = [{"error": str(e)}]

    context = {
        "reading": reading,
        "prompts": prompts,
        "outputs": outputs,
    }
    return render(request, "admin/compare_reading_prompts.html", context) 