const beginButton = document.querySelector('#begin-button');
const resetButton = document.querySelector('#reset-button');
const results = document.querySelector('#results');
const stages = [...document.querySelectorAll('.pipeline li')];
const progressBar = document.querySelector('#progress-bar');
const liveUpdate = document.querySelector('#live-update');
const runStatus = document.querySelector('#run-status');
const runId = document.querySelector('#run-id');
const trail = document.querySelector('#research-trail');
const dialog = document.querySelector('#archive-dialog');
const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
const sourceForm = document.querySelector('#source-form');
const sourceUrl = document.querySelector('#source-url');
const inspectButton = document.querySelector('#inspect-button');
const sourceFormStatus = document.querySelector('#source-form-status');
const liveRecord = document.querySelector('#live-record');
const analyzeButton = document.querySelector('#analyze-button');
const analysisStatus = document.querySelector('#analysis-status');
const aiAnalysis = document.querySelector('#ai-analysis');
const explorationLaunch = document.querySelector('#exploration-launch');
const exploreButton = document.querySelector('#explore-button');
const explorationStatus = document.querySelector('#exploration-status');
const followUpExpedition = document.querySelector('#follow-up-expedition');
const archiveLaunch = document.querySelector('#archive-launch');
const archiveButton = document.querySelector('#archive-button');
const archiveSaveStatus = document.querySelector('#archive-save-status');
const archiveConfirmation = document.querySelector('#archive-confirmation');

let discoveries = [];
let currentDiscovery = null;
let lastIndex = -1;
let running = false;
let runCounter = 0;
let currentEvidence = null;
let currentAnalysis = null;
let currentExploration = null;

const stageMessages = [
  'Seed framed: early personal web culture.',
  'Scout surfaced a plausible archival trace.',
  'A related reference opened a bounded lead.',
  'The candidate passed the relevance threshold.',
  'Uncertainty and provenance labels were attached.',
  'The discovery was preserved as a local record.'
];

const wait = (milliseconds) => new Promise(resolve => window.setTimeout(resolve, milliseconds));

async function loadDiscoveries() {
  const response = await fetch('data/sample-discoveries.json');
  if (!response.ok) throw new Error('The local discovery archive could not be loaded.');
  discoveries = await response.json();
}

function chooseDiscovery() {
  if (discoveries.length === 1) return discoveries[0];
  let nextIndex = Math.floor(Math.random() * discoveries.length);
  while (nextIndex === lastIndex) nextIndex = Math.floor(Math.random() * discoveries.length);
  lastIndex = nextIndex;
  return discoveries[nextIndex];
}

function setRunState(label, active = false) {
  runStatus.innerHTML = `<span class="status-indicator"></span>${label}`;
  runStatus.querySelector('.status-indicator').style.background = active ? 'var(--amber)' : 'var(--moss)';
}

function resetPipeline() {
  stages.forEach(stage => stage.classList.remove('active', 'complete'));
  progressBar.style.width = '0%';
  liveUpdate.textContent = 'Awaiting expedition.';
  runId.textContent = 'RUN —';
  setRunState('Ready / simulated');
}

function buildTrail(discovery) {
  trail.innerHTML = '';
  discovery.observations.forEach((observation, index) => {
    const item = document.createElement('li');
    const number = String(index + 1).padStart(2, '0');
    item.innerHTML = `<span class="observation-number">OBS ${number}</span><div class="observation-copy"><strong>${observation.action}</strong><span>${observation.detail}</span></div>`;
    trail.append(item);
  });
}

function revealDiscovery(discovery) {
  document.querySelector('#result-type').textContent = discovery.type;
  document.querySelector('#result-era').textContent = `Approx. era · ${discovery.era}`;
  document.querySelector('#result-title').textContent = discovery.title;
  document.querySelector('#result-summary').textContent = discovery.summary;
  document.querySelector('#result-why').textContent = discovery.why_interesting;
  document.querySelector('#result-confidence').textContent = discovery.confidence;
  document.querySelector('#result-status').textContent = discovery.archive_status;
  document.querySelector('#result-trail-count').textContent = `${discovery.observations.length} observations`;
  buildTrail(discovery);
  results.hidden = false;
}

function populateDialog(discovery) {
  document.querySelector('#archive-title').textContent = discovery.title;
  document.querySelector('#archive-summary').textContent = discovery.summary;
  document.querySelector('#archive-notes').textContent = discovery.research_notes;
  document.querySelector('#archive-source').textContent = `${discovery.source_type}. ${discovery.provenance}`;
  document.querySelector('#archive-uncertain').textContent = discovery.uncertainty;
  document.querySelector('#archive-followup').textContent = discovery.follow_up;
  document.querySelector('#archive-confidence').textContent = discovery.confidence;
  document.querySelector('#archive-status').textContent = discovery.archive_status;
  document.querySelector('#archive-timestamp').textContent = discovery.archived_timestamp;
}

async function beginExpedition() {
  if (running) return;
  running = true;
  beginButton.disabled = true;
  results.hidden = true;
  resetPipeline();
  runCounter += 1;
  runId.textContent = `RUN ${String(runCounter).padStart(3, '0')} / SIM`;
  setRunState('In progress / simulated', true);
  const delay = reducedMotion.matches ? 40 : 560;

  try {
    if (!discoveries.length) await loadDiscoveries();
    currentDiscovery = chooseDiscovery();

    for (let index = 0; index < stages.length; index += 1) {
      if (index > 0) stages[index - 1].classList.replace('active', 'complete');
      stages[index].classList.add('active');
      progressBar.style.width = `${((index + 1) / stages.length) * 100}%`;
      liveUpdate.textContent = stageMessages[index];
      await wait(delay);
    }

    stages.at(-1).classList.replace('active', 'complete');
    revealDiscovery(currentDiscovery);
    setRunState('Complete / archived locally');
    liveUpdate.textContent = `Expedition complete: “${currentDiscovery.title}” was archived.`;
    results.scrollIntoView({ behavior: reducedMotion.matches ? 'auto' : 'smooth', block: 'start' });
  } catch (error) {
    liveUpdate.textContent = error.message;
    setRunState('Local data unavailable');
  } finally {
    beginButton.disabled = false;
    running = false;
  }
}

beginButton.addEventListener('click', beginExpedition);
resetButton.addEventListener('click', () => {
  results.hidden = true;
  beginExpedition();
});
document.querySelector('#open-record').addEventListener('click', () => {
  if (!currentDiscovery) return;
  populateDialog(currentDiscovery);
  dialog.showModal();
});
document.querySelector('#close-record').addEventListener('click', () => dialog.close());
document.querySelector('#dialog-done').addEventListener('click', () => dialog.close());
dialog.addEventListener('click', event => {
  if (event.target === dialog) dialog.close();
});

function setText(selector, value) {
  document.querySelector(selector).textContent = value;
}

function setSourceLink(selector, value) {
  const link = document.querySelector(selector);
  link.textContent = value;
  link.href = value;
}

function resetAnalysisState() {
  currentAnalysis = null;
  currentExploration = null;
  aiAnalysis.hidden = true;
  document.querySelector('#no-analysis').hidden = false;
  analysisStatus.className = 'analysis-status';
  analysisStatus.textContent = '';
  analyzeButton.disabled = false;
  analyzeButton.removeAttribute('aria-busy');
  analyzeButton.childNodes[0].textContent = 'Analyze Evidence ';
  explorationLaunch.hidden = true;
  followUpExpedition.hidden = true;
  explorationStatus.className = 'exploration-status';
  explorationStatus.textContent = '';
  exploreButton.disabled = false;
  exploreButton.removeAttribute('aria-busy');
  exploreButton.childNodes[0].textContent = 'Explore Leads ';
  archiveLaunch.hidden = true;
  archiveConfirmation.hidden = true;
  archiveSaveStatus.className = 'archive-save-status';
  archiveSaveStatus.textContent = '';
  archiveButton.disabled = false;
  archiveButton.removeAttribute('aria-busy');
  archiveButton.childNodes[0].textContent = 'Archive Research Run ';
}

function prepareArchiveAction() {
  archiveLaunch.hidden = false;
  archiveConfirmation.hidden = true;
  archiveSaveStatus.className = 'archive-save-status';
  archiveSaveStatus.textContent = '';
  archiveButton.disabled = false;
  archiveButton.removeAttribute('aria-busy');
  archiveButton.childNodes[0].textContent = 'Archive Research Run ';
}

function renderLiveEvidence(evidence) {
  const { source, content, links } = evidence;
  currentEvidence = evidence;
  resetAnalysisState();
  setText('#live-record-title', content.title || 'Untitled public page');
  setText('#live-status-code', String(source.status_code));
  setSourceLink('#live-requested-url', source.requested_url);
  setSourceLink('#live-final-url', source.final_url);
  setText('#live-retrieved-at', new Date(source.retrieved_at).toLocaleString());
  setText('#live-content-type', source.content_type);
  setText('#live-excerpt', content.text_excerpt || 'No visible text was extracted from this page.');
  setText('#live-text-length', `${content.text_length.toLocaleString()} extracted characters`);
  setText('#live-links-count', `${links.found} found`);

  const list = document.querySelector('#candidate-links');
  const empty = document.querySelector('#empty-leads');
  list.replaceChildren();
  links.candidates.forEach((candidate, index) => {
    const item = document.createElement('li');
    const number = document.createElement('span');
    const anchor = document.createElement('a');
    number.textContent = String(index + 1).padStart(2, '0');
    anchor.textContent = candidate;
    anchor.href = candidate;
    anchor.target = '_blank';
    anchor.rel = 'noopener noreferrer nofollow';
    item.append(number, anchor);
    list.append(item);
  });
  empty.hidden = links.candidates.length > 0;
  liveRecord.hidden = false;
}

function appendAnalysisListItems(selector, values) {
  const list = document.querySelector(selector);
  list.replaceChildren();
  values.forEach(value => {
    const item = document.createElement('li');
    item.textContent = value;
    list.append(item);
  });
}

function renderAnalysis(analysis) {
  currentAnalysis = analysis;
  setText('#analysis-page-type', analysis.page_type);
  setText('#analysis-summary', analysis.summary);
  setText('#analysis-interest', analysis.why_interesting);
  setText('#analysis-decision', analysis.archive_recommendation.decision.toUpperCase());
  setText('#analysis-decision-reason', analysis.archive_recommendation.reason);
  setText('#analysis-confidence', analysis.confidence.toUpperCase());

  const observations = document.querySelector('#analysis-observations');
  observations.replaceChildren();
  analysis.observations.forEach((observation, index) => {
    const item = document.createElement('li');
    const number = document.createElement('span');
    const copy = document.createElement('div');
    const claim = document.createElement('p');
    const evidence = document.createElement('small');
    number.textContent = String(index + 1).padStart(2, '0');
    claim.textContent = observation.claim;
    evidence.textContent = `Evidence: ${observation.evidence}`;
    copy.append(claim, evidence);
    item.append(number, copy);
    observations.append(item);
  });

  appendAnalysisListItems('#analysis-uncertainties', analysis.uncertainties);
  const followUps = document.querySelector('#analysis-followups');
  const noFollowUps = document.querySelector('#analysis-no-followups');
  followUps.replaceChildren();
  analysis.candidate_follow_ups.forEach(followUp => {
    const item = document.createElement('li');
    const link = document.createElement('a');
    const reason = document.createElement('p');
    link.textContent = followUp.url;
    link.href = followUp.url;
    link.target = '_blank';
    link.rel = 'noopener noreferrer nofollow';
    reason.textContent = followUp.reason;
    item.append(link, reason);
    followUps.append(item);
  });
  noFollowUps.hidden = analysis.candidate_follow_ups.length > 0;
  document.querySelector('#no-analysis').hidden = true;
  aiAnalysis.hidden = false;
  explorationLaunch.hidden = !(currentEvidence && currentEvidence.links.candidates.length > 0);
  prepareArchiveAction();
}

function externalLink(url, label = url) {
  const link = document.createElement('a');
  link.textContent = label;
  link.href = url;
  link.target = '_blank';
  link.rel = 'noopener noreferrer nofollow';
  return link;
}

function detailSection(label, value) {
  const section = document.createElement('section');
  const heading = document.createElement('h5');
  const copy = document.createElement('p');
  heading.textContent = label;
  copy.textContent = value;
  section.append(heading, copy);
  return section;
}

function provenanceItem(label, value) {
  const item = document.createElement('div');
  const heading = document.createElement('span');
  const copy = document.createElement('strong');
  heading.textContent = label;
  copy.textContent = value;
  item.append(heading, copy);
  return item;
}

function renderFollowUp(item, index) {
  const retrieved = item.retrieval.status === 'success';
  const succeeded = retrieved && item.analysis_status.status === 'success';
  const card = document.createElement('article');
  card.className = `follow-up-card${succeeded ? '' : ' failed'}`;

  const head = document.createElement('div');
  head.className = 'follow-up-card-head';
  const number = document.createElement('span');
  number.className = 'follow-up-index';
  number.textContent = String(index + 1).padStart(2, '0');
  const identity = document.createElement('div');
  const title = document.createElement('h4');
  title.textContent = retrieved ? item.evidence.content.title : 'Follow-up unavailable';
  identity.append(title, externalLink(item.url));
  const status = document.createElement('span');
  status.className = 'retrieval-status';
  status.textContent = succeeded ? 'Retrieved + analyzed' : retrieved ? 'Analysis failed' : 'Retrieval failed';
  head.append(number, identity, status);

  const details = document.createElement('div');
  details.className = 'follow-up-details';
  details.append(
    detailSection('Why selected', item.selection_reason),
    detailSection('Short analysis', succeeded ? item.summary : retrieved ? item.analysis_status.error.message : item.retrieval.error.message),
    detailSection('What it added', succeeded ? item.what_it_added : 'No evidence was added from this page.'),
    detailSection('Confidence', succeeded ? item.confidence.toUpperCase() : 'NOT ASSESSED')
  );
  card.append(head, details);

  if (retrieved) {
    const { source, content, links } = item.evidence;
    const provenance = document.createElement('div');
    provenance.className = 'follow-up-provenance';
    provenance.append(
      provenanceItem('Requested URL', source.requested_url),
      provenanceItem('Final URL', source.final_url),
      provenanceItem('Retrieved', new Date(source.retrieved_at).toLocaleString()),
      provenanceItem('HTTP status', String(source.status_code)),
      provenanceItem('Content type', source.content_type),
      provenanceItem('Candidate links', `${links.candidates.length} retained / unvisited`)
    );
    const excerpt = detailSection('Separated evidence excerpt', content.text_excerpt || 'No visible text was extracted.');
    excerpt.className = 'follow-up-excerpt';
    card.append(provenance, excerpt);
  }
  return card;
}

function renderSynthesis(expedition) {
  const panel = document.querySelector('#research-synthesis');
  if (!expedition.synthesis) {
    panel.hidden = true;
    return;
  }
  const synthesis = expedition.synthesis;
  setText('#synthesis-starting-point', expedition.starting_point);
  setText('#synthesis-changed', synthesis.what_changed);
  appendAnalysisListItems('#synthesis-confirmed', synthesis.what_was_confirmed);
  appendAnalysisListItems('#synthesis-uncertain', synthesis.what_remains_uncertain);
  setText('#synthesis-value', synthesis.research_value.toUpperCase());

  const nextLead = document.querySelector('#synthesis-next-lead');
  nextLead.replaceChildren();
  if (synthesis.best_next_lead.url) {
    const label = document.createElement('p');
    label.append(externalLink(synthesis.best_next_lead.url));
    const reason = document.createElement('p');
    reason.textContent = synthesis.best_next_lead.reason;
    nextLead.append(label, reason);
  } else {
    const copy = document.createElement('p');
    copy.textContent = synthesis.best_next_lead.reason;
    nextLead.append(copy);
  }
  panel.hidden = false;
}

function renderExpedition(expedition) {
  const list = document.querySelector('#follow-up-list');
  list.replaceChildren(...expedition.explored.map(renderFollowUp));
  renderSynthesis(expedition);
  followUpExpedition.hidden = false;
  if (expedition.synthesis) {
    currentExploration = expedition;
    prepareArchiveAction();
  }
}

sourceForm.addEventListener('submit', async event => {
  event.preventDefault();
  inspectButton.disabled = true;
  inspectButton.setAttribute('aria-busy', 'true');
  sourceFormStatus.className = 'source-form-status loading';
  sourceFormStatus.textContent = 'Retrieving one bounded public page…';
  liveRecord.hidden = true;
  currentEvidence = null;

  try {
    const response = await fetch('/api/ingest', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url: sourceUrl.value })
    });
    const payload = await response.json();
    if (!response.ok || !payload.ok) {
      throw new Error(payload.error?.message || 'The source could not be inspected safely.');
    }
    renderLiveEvidence(payload.evidence);
    sourceFormStatus.className = 'source-form-status success';
    sourceFormStatus.textContent = 'Retrieval complete. Evidence is shown below.';
    liveRecord.scrollIntoView({ behavior: reducedMotion.matches ? 'auto' : 'smooth', block: 'start' });
  } catch (error) {
    sourceFormStatus.className = 'source-form-status error';
    sourceFormStatus.textContent = error.message;
    sourceUrl.focus();
  } finally {
    inspectButton.disabled = false;
    inspectButton.removeAttribute('aria-busy');
  }
});

analyzeButton.addEventListener('click', async () => {
  if (!currentEvidence) return;
  analyzeButton.disabled = true;
  analyzeButton.setAttribute('aria-busy', 'true');
  analysisStatus.className = 'analysis-status loading';
  analysisStatus.textContent = 'Analyzing only the supplied evidence…';
  aiAnalysis.hidden = true;
  explorationLaunch.hidden = true;
  followUpExpedition.hidden = true;
  currentAnalysis = null;

  let completed = false;
  try {
    const response = await fetch('/api/analyze', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ evidence: currentEvidence })
    });
    const payload = await response.json();
    if (!response.ok || !payload.ok) {
      throw new Error(payload.error?.message || 'The evidence could not be analyzed safely.');
    }
    renderAnalysis(payload.analysis);
    completed = true;
    analysisStatus.className = 'analysis-status success';
    analysisStatus.textContent = 'Schema-constrained analysis validated.';
    analyzeButton.childNodes[0].textContent = 'Analysis Complete ';
    aiAnalysis.scrollIntoView({ behavior: reducedMotion.matches ? 'auto' : 'smooth', block: 'start' });
  } catch (error) {
    analysisStatus.className = 'analysis-status error';
    analysisStatus.textContent = error.message;
    document.querySelector('#no-analysis').hidden = false;
  } finally {
    analyzeButton.removeAttribute('aria-busy');
    analyzeButton.disabled = completed;
  }
});

exploreButton.addEventListener('click', async () => {
  if (!currentEvidence || !currentAnalysis || currentEvidence.links.candidates.length === 0) return;
  exploreButton.disabled = true;
  exploreButton.setAttribute('aria-busy', 'true');
  explorationStatus.className = 'exploration-status loading';
  explorationStatus.textContent = 'Selecting and inspecting up to 2 follow-up pages…';
  followUpExpedition.hidden = true;

  let completed = false;
  try {
    const response = await fetch('/api/explore', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ evidence: currentEvidence, analysis: currentAnalysis })
    });
    const payload = await response.json();
    if (payload.expedition) {
      renderExpedition(payload.expedition);
      completed = true;
      exploreButton.childNodes[0].textContent = 'Expedition Complete ';
      explorationStatus.className = response.ok ? 'exploration-status success' : 'exploration-status error';
      explorationStatus.textContent = response.ok
        ? `Expedition stopped after ${payload.expedition.explored.length} selected follow-up page${payload.expedition.explored.length === 1 ? '' : 's'}.`
        : payload.expedition.failure?.message || 'The expedition stopped safely with partial evidence.';
      followUpExpedition.scrollIntoView({ behavior: reducedMotion.matches ? 'auto' : 'smooth', block: 'start' });
      return;
    }
    throw new Error(payload.error?.message || 'The follow-up expedition could not be completed safely.');
  } catch (error) {
    explorationStatus.className = 'exploration-status error';
    explorationStatus.textContent = error.message;
  } finally {
    exploreButton.removeAttribute('aria-busy');
    exploreButton.disabled = completed;
  }
});

archiveButton.addEventListener('click', async () => {
  if (!currentEvidence || !currentAnalysis) return;
  archiveButton.disabled = true;
  archiveButton.setAttribute('aria-busy', 'true');
  archiveSaveStatus.className = 'archive-save-status loading';
  archiveSaveStatus.textContent = 'Saving a validated public research record…';
  archiveConfirmation.hidden = true;

  let completed = false;
  try {
    const researchRun = { evidence: currentEvidence, analysis: currentAnalysis };
    if (currentExploration) researchRun.exploration = currentExploration;
    const response = await fetch('/api/archive', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(researchRun)
    });
    const payload = await response.json();
    if (!response.ok || !payload.ok) {
      throw new Error(payload.error?.message || 'The research run could not be archived safely.');
    }
    completed = true;
    archiveSaveStatus.className = 'archive-save-status';
    archiveSaveStatus.textContent = payload.duplicate ? 'This identical run was already archived recently.' : '';
    setText('#archived-public-id', payload.public_id);
    const recordLink = document.querySelector('#open-archived-record');
    recordLink.href = payload.archive_url;
    archiveConfirmation.hidden = false;
    archiveButton.childNodes[0].textContent = 'Research Run Archived ';
  } catch (error) {
    archiveSaveStatus.className = 'archive-save-status error';
    archiveSaveStatus.textContent = error.message;
  } finally {
    archiveButton.removeAttribute('aria-busy');
    archiveButton.disabled = completed;
  }
});

loadDiscoveries().catch(error => {
  liveUpdate.textContent = error.message;
  beginButton.disabled = true;
});
