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

let discoveries = [];
let currentDiscovery = null;
let lastIndex = -1;
let running = false;
let runCounter = 0;

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

function renderLiveEvidence(evidence) {
  const { source, content, links } = evidence;
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

sourceForm.addEventListener('submit', async event => {
  event.preventDefault();
  inspectButton.disabled = true;
  inspectButton.setAttribute('aria-busy', 'true');
  sourceFormStatus.className = 'source-form-status loading';
  sourceFormStatus.textContent = 'Retrieving one bounded public page…';
  liveRecord.hidden = true;

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

loadDiscoveries().catch(error => {
  liveUpdate.textContent = error.message;
  beginButton.disabled = true;
});
