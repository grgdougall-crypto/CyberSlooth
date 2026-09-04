const evaluateButton = document.querySelector("#evaluate-candidates");
const statusLine = document.querySelector("#evaluation-status");
const results = document.querySelector("#daily-results");
const emptyState = document.querySelector("#daily-empty");
const winner = document.querySelector("#candidate-winner");
const rankingList = document.querySelector("#candidate-ranking-list");

function appendTextElement(parent, tag, text, className) {
  const element = document.createElement(tag);
  element.textContent = text;
  if (className) element.className = className;
  parent.append(element);
  return element;
}

function renderEvaluation(data) {
  winner.replaceChildren();
  const meta = document.createElement("div");
  appendTextElement(meta, "span", "Daily Discovery Candidate", "candidate-label");
  appendTextElement(meta, "span", `Rank 01 · ${data.selected.total_score}/25`);
  winner.append(meta);
  const heading = document.createElement("h3");
  const link = document.createElement("a");
  link.href = data.selected.archive_url;
  link.textContent = data.selected.title;
  heading.append(link);
  winner.append(heading);
  appendTextElement(
    winner,
    "p",
    `${data.selected.public_id} · ${data.selected.archive_decision.toUpperCase()} · ${data.selected.confidence.toUpperCase()} CONFIDENCE`,
    "candidate-id",
  );
  appendTextElement(winner, "p", data.selection_reason);
  appendTextElement(winner, "small", `Evaluated ${new Date(data.evaluated_at).toLocaleString()}`);
  const openLink = document.createElement("a");
  openLink.className = "open-record candidate-open";
  openLink.href = data.selected.archive_url;
  openLink.textContent = "Open Record →";
  winner.append(openLink);

  rankingList.replaceChildren();
  data.ranked.forEach((record) => {
    const item = document.createElement("li");
    appendTextElement(item, "span", String(record.rank).padStart(2, "0"));
    const copy = document.createElement("div");
    const recordLink = document.createElement("a");
    recordLink.href = record.archive_url;
    recordLink.textContent = record.title;
    copy.append(recordLink);
    appendTextElement(
      copy,
      "small",
      `Value ${record.research_value_score} · Evidence ${record.evidence_quality_score} · Novelty ${record.novelty_score} · Interest ${record.interestingness_score} · Archive ${record.archive_quality_score} · Uncertainty −${record.uncertainty_penalty}`,
    );
    appendTextElement(copy, "p", record.reason);
    item.append(copy);
    appendTextElement(item, "strong", `${record.total_score}/25`);
    rankingList.append(item);
  });
  emptyState.hidden = true;
  results.hidden = false;
}

if (evaluateButton) {
  evaluateButton.addEventListener("click", async () => {
    evaluateButton.disabled = true;
    evaluateButton.setAttribute("aria-busy", "true");
    statusLine.textContent = "Evaluating the most recent archived discoveries…";
    try {
      const response = await fetch("/api/select-daily-candidate", { method: "POST" });
      const data = await response.json();
      if (!response.ok || !data.ok) throw new Error(data.error?.message || "Evaluation could not be completed.");
      renderEvaluation(data);
      statusLine.textContent = `Selected ${data.selected_public_id}. No publishing action was taken.`;
    } catch (error) {
      statusLine.textContent = error instanceof Error ? error.message : "Evaluation could not be completed.";
    } finally {
      evaluateButton.disabled = false;
      evaluateButton.removeAttribute("aria-busy");
    }
  });
}
