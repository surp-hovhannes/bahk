"use strict";

// The correction choice is needed only when a reviewer reports a problem, and a
// note only for the decisions listed on the select. Without JavaScript the
// fields stay visible and server validation still applies.
(() => {
    const decision = document.getElementById("review-action");
    if (!decision) return;
    const correction = document.getElementById("review-correction");
    const correctDecision = document.getElementById("review-expected");
    const note = document.getElementById("review-note");
    const noteLabel = document.getElementById("review-note-label");
    const noteRequiredFor = (decision.dataset.noteRequired || "").split(" ").filter(Boolean);

    const update = () => {
        if (correction && correctDecision) {
            const reportingProblem = decision.value === "misclassification";
            correction.hidden = !reportingProblem;
            correctDecision.disabled = !reportingProblem;
            correctDecision.required = reportingProblem;
            correctDecision.setAttribute("aria-required", String(reportingProblem));
            correction.querySelector("label").classList.toggle("required", reportingProblem);
        }
        if (note && noteLabel) {
            const needsNote = noteRequiredFor.includes(decision.value);
            note.setAttribute("aria-required", String(needsNote));
            noteLabel.classList.toggle("required", needsNote);
        }
    };
    decision.addEventListener("change", update);
    update();
})();
