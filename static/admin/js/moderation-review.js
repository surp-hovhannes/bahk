"use strict";

// The correction choice is needed only when a reviewer reports a problem.
// Without JavaScript the field remains visible and server validation still applies.
(() => {
    const decision = document.getElementById("review-action");
    const correction = document.getElementById("review-correction");
    const correctDecision = document.getElementById("review-expected");
    if (!decision || !correction || !correctDecision) return;

    const updateCorrection = () => {
        const reportingProblem = decision.value === "misclassification";
        correction.hidden = !reportingProblem;
        correctDecision.disabled = !reportingProblem;
        correctDecision.required = reportingProblem;
        correctDecision.setAttribute("aria-required", String(reportingProblem));
        correction.querySelector("label").classList.toggle("required", reportingProblem);
    };
    decision.addEventListener("change", updateCorrection);
    updateCorrection();
})();
