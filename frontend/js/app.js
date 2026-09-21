import { api } from './api.js';
import { VoiceAssistant } from './voice.js';

// Application State
const state = {
  currentRole: 'patient',
  activeHospitalId: null,
  activeDoctorId: null,
  patientId: null,
  sessionId: `voice-sess-${Date.now()}-${Math.random().toString(36).substring(2, 7)}`,
  selectedSlot: null,
  selectedDoctor: null,
  activeAppointment: null,
  chaosSetting: { mode: 'TIMEOUT_AFTER_SAVE', failure_active: true }
};

let currentLoadingBubble = null;
let currentFillerBubble = null;

function showThinkingState() {
  if (!currentLoadingBubble) {
    currentLoadingBubble = appendChatBubble('ai', '<span class="sync-icon-spin">⏳</span> Thinking & accessing authorized capabilities...', true);
  }
}

function hideThinkingState() {
  if (currentLoadingBubble) {
    currentLoadingBubble.remove();
    currentLoadingBubble = null;
  }
  if (currentFillerBubble) {
    currentFillerBubble.remove();
    currentFillerBubble = null;
  }
}

// Initialize Voice Assistant
const voiceAssistant = new VoiceAssistant({
  sessionId: state.sessionId,
  patientId: state.patientId,
  hospitalId: state.activeHospitalId,
  onTranscript: (transcript, isFinal) => {
    if (typeof voiceAssistant !== 'undefined' && voiceAssistant && (voiceAssistant.isSpeaking || voiceAssistant.isRecognitionPausedForTTS || (Date.now() - voiceAssistant.lastSpeechEndTime < 1000))) {
      return;
    }
    const input = document.getElementById('chat-input');
    if (input) input.value = transcript;
    // Real-time preview in input box without prematurely cutting off turns
  },
  onTurnSubmitted: (transcript) => {
    const input = document.getElementById('chat-input');
    if (input) input.value = '';
    appendChatBubble('patient', transcript);
    updateWorkflowStep(6); // Step 6: Patient voice conversation
    showThinkingState();
  },
  onStateChange: ({ isListening, isSpeaking, isThinking, callState, connectionMode, statusText }) => {
    const micBtn = document.getElementById('mic-btn');
    const inputMicBtn = document.getElementById('btn-input-mic');
    const statusTextEl = document.getElementById('voice-status-text');
    const connBadge = document.getElementById('voice-conn-badge');

    if (micBtn) {
      if (isListening) micBtn.classList.add('listening');
      else micBtn.classList.remove('listening');
    }
    if (inputMicBtn) {
      if (isListening) inputMicBtn.classList.add('listening');
      else inputMicBtn.classList.remove('listening');
    }
    if (statusTextEl) statusTextEl.innerText = statusText;

    if (connBadge) {
      if (connectionMode === 'WS') {
        connBadge.className = 'status-pill info';
        connBadge.innerText = '🟢 Streaming WS';
      } else if (connectionMode === 'REST') {
        connBadge.className = 'status-pill';
        connBadge.innerText = '🔄 REST Mode';
      }
    }
  },
  onInterimFiller: (payload) => {
    // Show acoustic filler in chat to visually indicate low latency
    const fillerText = payload.filler_text || payload.spoken_filler;
    if (currentLoadingBubble) {
      currentLoadingBubble.innerHTML = `<span class="sync-icon-spin">⏳</span> <em>"${fillerText}"</em>`;
    }
  },
  onAssistantSpeech: (payload) => {
    hideThinkingState();
    handleAgentSpeechResponse(payload);
  },
  onInterrupted: () => {
    const bargeEl = document.getElementById('bargein-indicator');
    if (bargeEl) {
      bargeEl.style.display = 'block';
      setTimeout(() => { bargeEl.style.display = 'none'; }, 2500);
    }
    hideThinkingState();
  },
  onEmergency: (payload) => {
    appendChatBubble('emergency', payload.reply || "EMERGENCY PROTOCOL ACTIVATED. Call 911 immediately.");
  }
});

// Update the 19-stage workflow indicator
function updateWorkflowStep(stepNumber) {
  const steps = document.querySelectorAll('.step-item');
  steps.forEach((el, index) => {
    const num = index + 1;
    el.classList.remove('active', 'completed');
    if (num < stepNumber) el.classList.add('completed');
    else if (num === stepNumber) el.classList.add('active');
  });
}

// Navigation between views
async function switchView(viewName, syncPersona = true) {
  state.currentRole = viewName;
  document.querySelectorAll('.nav-tab-btn').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.view === viewName);
  });
  document.querySelectorAll('.view-section').forEach(sec => {
    sec.classList.toggle('active', sec.id === `${viewName}-section`);
  });

  if (syncPersona) {
    if (viewName === 'patient' && state.currentPersona !== 'PATIENT') {
      await switchPersona('PATIENT', null, null, false);
    } else if (viewName === 'doctor' && state.currentPersona !== 'DOCTOR') {
      await switchPersona('DOCTOR', null, null, false);
    } else if (viewName === 'hospital-admin' && state.currentPersona !== 'HOSPITAL_ADMIN') {
      await switchPersona('HOSPITAL_ADMIN', null, null, false);
    } else if (viewName === 'admin' && state.currentPersona !== 'PLATFORM_ADMIN') {
      await switchPersona('PLATFORM_ADMIN', null, null, false);
    }
  }

  if (viewName === 'patient') loadPatientDashboard();
  else if (viewName === 'doctor') loadDoctorView();
  else if (viewName === 'hospital-admin') loadHospitalAdminDashboard();
  else if (viewName === 'admin') loadAdminView();
  else if (viewName === 'hospital') loadHospitalView();
}
window.switchView = switchView;

// ----------------- PATIENT VOICE & TRIAGE FLOW -----------------
async function handleSendMessage(messageText = null) {
  const input = document.getElementById('chat-input');
  const text = messageText || (input ? input.value.trim() : '');
  if (!text) return;
  if (input) input.value = '';

  // Instant barge-in: cancel any ongoing speech output
  voiceAssistant.cancelSpeech();

  // Append user message to transcript
  appendChatBubble('patient', text);
  updateWorkflowStep(6); // Step 6: Patient voice conversation

  showThinkingState();

  try {
    if (voiceAssistant.ws && voiceAssistant.ws.readyState === WebSocket.OPEN) {
      voiceAssistant.submitTurn(text);
    } else {
      const res = await api.voiceTurn(
        voiceAssistant.sessionId,
        text,
        state.patientId,
        state.activeHospitalId,
        'web_voice'
      );
      hideThinkingState();
      handleAgentSpeechResponse(res);
      if (res.spoken_text) {
        voiceAssistant.speak(res.spoken_text);
      }
    }
  } catch (err) {
    hideThinkingState();
    appendChatBubble('ai', `Error interacting with AI agent: ${err.message}`);
  }
}

function handleAgentSpeechResponse(res) {
  // 1. Show latency badge
  const latencyBadge = document.getElementById('voice-latency-badge');
  if (latencyBadge && res.latency_ms !== undefined) {
    latencyBadge.innerText = `⏱️ ${res.latency_ms}ms`;
    latencyBadge.style.display = 'inline-block';
  }

  // 2. Build rich bubble with capability badges
  let capBadgesHtml = '';
  if (res.capabilities_executed && res.capabilities_executed.length > 0) {
    capBadgesHtml = `
      <div style="margin-top: 0.5rem; display: flex; flex-wrap: wrap; gap: 0.35rem; font-size: 0.75rem;">
        <span style="color: var(--text-dim); align-self: center;">⚡ Capabilities:</span>
        ${res.capabilities_executed.map(c => `<span class="meta-badge" style="background: rgba(6, 182, 212, 0.15); color: var(--accent-cyan); border: 1px solid rgba(6, 182, 212, 0.3);">${c}</span>`).join('')}
      </div>
    `;
  }

  if (res.capabilities_executed && res.capabilities_executed.includes('verify_external_appointment')) {
    capBadgesHtml += `
      <div style="margin-top: 0.4rem; display: flex; align-items: center; gap: 0.4rem; color: var(--accent-emerald); font-size: 0.8rem; font-weight: 600;">
        <span>🔒 External EHR Verified & State Synchronized</span>
      </div>
    `;
  }

  const rawReply = res.reply || res.spoken_text || '';
  const bubbleHtml = `
    <div>${rawReply.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>').replace(/\n/g, '<br/>')}</div>
    ${capBadgesHtml}
  `;

  if (res.is_emergency) {
    appendChatBubble('emergency', bubbleHtml);
  } else {
    appendChatBubble('ai', bubbleHtml);
  }

  // 3. Update workflow step according to intent
  const intent = res.intent || '';
  if (intent === 'SEARCH_DOCTORS') {
    updateWorkflowStep(8); // Doctor discovery
    refreshDoctorDiscovery();
  } else if (intent === 'CHECK_AVAILABILITY') {
    updateWorkflowStep(9); // Real availability check
    refreshDoctorDiscovery();
  } else if (intent === 'CREATE_APPOINTMENT') {
    updateWorkflowStep(13); // Appointment Confirmed
    refreshDoctorDiscovery();
    loadPatientDashboard();
    loadDoctorView();
    loadAdminView();
    loadHospitalAdminDashboard();
  } else if (intent === 'CANCEL_APPOINTMENT') {
    updateWorkflowStep(17); // Cancellation
    loadDoctorView();
    loadAdminView();
  } else if (intent === 'TRANSFER_TO_HUMAN' || res.is_escalated) {
    updateWorkflowStep(18); // Human escalation
  } else {
    updateWorkflowStep(7); // Intent understanding
  }
}

function appendChatBubble(type, content, isLoading = false) {
  const transcriptBox = document.getElementById('chat-transcript');
  if (!transcriptBox) return null;

  const bubble = document.createElement('div');
  bubble.className = `chat-bubble ${type}`;
  bubble.innerHTML = content;
  transcriptBox.appendChild(bubble);
  transcriptBox.scrollTop = transcriptBox.scrollHeight;
  return bubble;
}

function renderTriageResults(triage) {
  const container = document.getElementById('triage-results-container');
  if (!container) return;

  if (triage.is_emergency) {
    container.innerHTML = `
      <div class="glass-panel" style="padding: 1.5rem; border-color: var(--accent-rose);">
        <h3 style="color: var(--accent-rose); margin-bottom: 0.5rem;">⚠️ Emergency Protocol Triggered</h3>
        <p style="color: #fecdd3;">The AI identified symptoms requiring urgent clinical evaluation. Please contact local emergency services (911) or proceed to the nearest emergency room.</p>
      </div>
    `;
    return;
  }

  const urgencyClass = `urgency-${triage.urgency_level.toLowerCase()}`;

  let doctorsHtml = '';
  if (triage.suggested_doctors && triage.suggested_doctors.length > 0) {
    doctorsHtml = triage.suggested_doctors.map(doc => {
      // Find slots for this doctor
      const docSlots = (triage.available_slots || []).filter(s => s.doctor_id === doc.id);
      
      const slotsHtml = docSlots.length > 0 ? docSlots.map(s => {
        const d = new Date(s.start_time);
        const timeStr = d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
        const dateStr = d.toLocaleDateString([], { month: 'short', day: 'numeric' });
        return `<button class="slot-btn" onclick="window.selectSlot('${s.id}', '${doc.id}', '${doc.full_name}', '${dateStr} at ${timeStr}', '${triage.chief_complaint}', '${triage.urgency_level}')">${dateStr} ${timeStr}</button>`;
      }).join('') : '<span style="color: var(--text-dim); font-size: 0.85rem;">No immediate slots. Check back soon.</span>';

      return `
        <div class="doctor-card" data-name="${(doc.full_name || '').toLowerCase()}" data-specialty="${(doc.specialty || '').toLowerCase()}" data-hospital="${(doc.hospital_name || '').toLowerCase()}">
          <div class="doc-info-header">
            <div style="display: flex; gap: 0.9rem; align-items: center;">
              <div class="doc-avatar">👨‍⚕️</div>
              <div>
                <h4>${doc.full_name}</h4>
                <div style="color: var(--accent-cyan); font-size: 0.85rem; font-weight: 600;">${doc.specialty}</div>
                <div style="color: var(--text-muted); font-size: 0.8rem;">${doc.hospital_name || 'Affiliated Hospital'} • $${doc.consultation_fee}</div>
              </div>
            </div>
            <span class="status-pill success">Verified</span>
          </div>
          <p style="color: var(--text-muted); font-size: 0.85rem;">${doc.bio || 'Specialist healthcare provider.'}</p>
          <div>
            <div style="font-size: 0.75rem; text-transform: uppercase; color: var(--text-dim); font-weight: 700; margin-bottom: 0.5rem;">Select an Available Slot (${docSlots.length}):</div>
            <div class="slot-pills-container">${slotsHtml}</div>
          </div>
        </div>
      `;
    }).join('');
  } else {
    doctorsHtml = `<p style="color: var(--text-muted); padding: 1.5rem; text-align: center;">No matching specialists available currently. Please register or contact support.</p>`;
  }

  const docCount = triage.suggested_doctors ? triage.suggested_doctors.length : 0;
  const countBadge = document.getElementById('doctor-count-badge');
  if (countBadge) countBadge.innerText = `${docCount} Available`;

  container.innerHTML = `
    <div class="triage-badge-row">
      <span class="meta-badge specialty">🩺 ${triage.specialty_recommended}</span>
      <span class="meta-badge ${urgencyClass}">⚡ Urgency: ${triage.urgency_level}</span>
      <span class="meta-badge" style="background: rgba(6, 182, 212, 0.15); color: var(--accent-cyan); border: 1px solid rgba(6, 182, 212, 0.3);">
        🏥 ${docCount} Doctors Available
      </span>
    </div>
    <div class="doctor-results-scroll-container" id="doctor-results-list">
      ${doctorsHtml}
    </div>
  `;
}

async function refreshDoctorDiscovery(specialty = null) {
  try {
    const container = document.getElementById('triage-results-container');
    if (!container) return;

    // Show loading state
    container.innerHTML = `
      <div style="color: var(--text-dim); text-align: center; padding: 3rem;">
        <span class="sync-icon-spin" style="font-size: 1.8rem; display: inline-block;">⏳</span>
        <p style="margin-top: 0.75rem; font-size: 0.9rem;">Checking real-time doctor availability & appointment slots...</p>
      </div>
    `;

    const doctors = await api.getDoctors(specialty);
    if (!doctors || doctors.length === 0) {
      container.innerHTML = `<p style="color: var(--text-muted); padding: 2rem; text-align: center;">No matching doctors found in current records.</p>`;
      const countBadge = document.getElementById('doctor-count-badge');
      if (countBadge) countBadge.innerText = `0 Available`;
      return;
    }

    const countBadge = document.getElementById('doctor-count-badge');
    if (countBadge) countBadge.innerText = `${doctors.length} Available`;

    // Fetch slots in batches of 25 for fast, responsive loading across hundreds of doctors
    const batchSize = 25;
    const docWithSlots = [];
    for (let i = 0; i < doctors.length; i += batchSize) {
      const batch = doctors.slice(i, i + batchSize);
      const batchResults = await Promise.all(batch.map(async (doc) => {
        try {
          const slots = await api.getDoctorSlots(doc.id);
          const availableSlots = (slots || []).filter(s => s.status === 'AVAILABLE');
          return { doc, availableSlots };
        } catch (_) {
          return { doc, availableSlots: [] };
        }
      }));
      docWithSlots.push(...batchResults);
    }

    let doctorsHtml = docWithSlots.map(({ doc, availableSlots }) => {
      const slotsHtml = availableSlots.length > 0 ? availableSlots.map(s => {
        const d = new Date(s.start_time);
        const timeStr = d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
        const dateStr = d.toLocaleDateString([], { month: 'short', day: 'numeric' });
        return `<button class="slot-btn" onclick="window.selectSlot('${s.id}', '${doc.id}', '${doc.full_name}', '${dateStr} at ${timeStr}', 'General Consultation', 'ROUTINE')">${dateStr} ${timeStr}</button>`;
      }).join('') : '<span style="color: var(--text-dim); font-size: 0.85rem;">No immediate slots available.</span>';

      return `
        <div class="doctor-card" data-name="${(doc.full_name || '').toLowerCase()}" data-specialty="${(doc.specialty || '').toLowerCase()}" data-hospital="${(doc.hospital_name || '').toLowerCase()}">
          <div class="doc-info-header">
            <div style="display: flex; gap: 0.9rem; align-items: center;">
              <div class="doc-avatar">👨‍⚕️</div>
              <div>
                <h4>${doc.full_name}</h4>
                <div style="color: var(--accent-cyan); font-size: 0.85rem; font-weight: 600;">${doc.specialty}</div>
                <div style="color: var(--text-muted); font-size: 0.8rem;">${doc.hospital_name || 'Hospital Affiliate'} • $${doc.consultation_fee}</div>
              </div>
            </div>
            <span class="status-pill success">Verified</span>
          </div>
          <p style="color: var(--text-muted); font-size: 0.85rem;">${doc.bio || 'Board certified specialist provider.'}</p>
          <div>
            <div style="font-size: 0.75rem; text-transform: uppercase; color: var(--text-dim); font-weight: 700; margin-bottom: 0.5rem;">Available Slots (${availableSlots.length}):</div>
            <div class="slot-pills-container">${slotsHtml}</div>
          </div>
        </div>
      `;
    }).join('');

    container.innerHTML = `
      <div class="triage-badge-row">
        <span class="meta-badge specialty">🩺 Specialists on Duty</span>
        <span class="meta-badge" style="background: rgba(6, 182, 212, 0.15); color: var(--accent-cyan); border: 1px solid rgba(6, 182, 212, 0.3);">
          🏥 ${doctors.length} Doctors Available
        </span>
      </div>
      <div class="doctor-results-scroll-container" id="doctor-results-list">
        ${doctorsHtml}
      </div>
    `;

    // Re-apply any existing filter
    filterDoctorCards();
  } catch (e) {
    console.warn("Could not refresh doctor discovery:", e);
  }
}
window.refreshDoctorDiscovery = refreshDoctorDiscovery;

// Doctor Search & Specialty Filter Handlers (Requirement 1 & 6)
window.handleDoctorSearchFilter = function(query) {
  const clearBtn = document.getElementById('doctor-filter-clear');
  if (clearBtn) {
    clearBtn.style.display = query && query.trim().length > 0 ? 'inline-block' : 'none';
  }
  filterDoctorCards();
};

window.clearDoctorSearchFilter = function() {
  const input = document.getElementById('doctor-search-input');
  if (input) input.value = '';
  const clearBtn = document.getElementById('doctor-filter-clear');
  if (clearBtn) clearBtn.style.display = 'none';
  filterDoctorCards();
};

window.handleDoctorSpecialtyFilter = function(specialty) {
  filterDoctorCards();
};

window.filterDoctorCards = function() {
  const searchInput = document.getElementById('doctor-search-input');
  const specialtySelect = document.getElementById('doctor-specialty-filter');
  const query = (searchInput ? searchInput.value : '').trim().toLowerCase();
  const selectedSpecialty = (specialtySelect ? specialtySelect.value : '').trim().toLowerCase();

  const cards = document.querySelectorAll('#doctor-results-list .doctor-card');
  if (!cards || cards.length === 0) return;

  let visibleCount = 0;
  cards.forEach(card => {
    const name = card.getAttribute('data-name') || '';
    const spec = card.getAttribute('data-specialty') || '';
    const hosp = card.getAttribute('data-hospital') || '';

    const matchesQuery = !query || name.includes(query) || spec.includes(query) || hosp.includes(query);
    const matchesSpecialty = !selectedSpecialty || spec.includes(selectedSpecialty);

    if (matchesQuery && matchesSpecialty) {
      card.style.display = 'flex';
      visibleCount++;
    } else {
      card.style.display = 'none';
    }
  });

  const countBadge = document.getElementById('doctor-count-badge');
  if (countBadge) {
    countBadge.innerText = `${visibleCount} of ${cards.length}`;
  }

  // Manage empty-filter placeholder inside list
  let noMatchEl = document.getElementById('doctor-filter-no-match');
  if (visibleCount === 0) {
    if (!noMatchEl) {
      const container = document.getElementById('doctor-results-list');
      if (container) {
        noMatchEl = document.createElement('div');
        noMatchEl.id = 'doctor-filter-no-match';
        noMatchEl.style.cssText = 'color: var(--text-dim); text-align: center; padding: 2rem; font-size: 0.9rem;';
        noMatchEl.innerHTML = '🔍 No doctors match the selected search or specialty criteria.';
        container.appendChild(noMatchEl);
      }
    } else {
      noMatchEl.style.display = 'block';
    }
  } else if (noMatchEl) {
    noMatchEl.style.display = 'none';
  }
};

// Slot selection & Booking Modal Trigger
window.selectSlot = async function(slotId, docId, docName, slotTimeFormatted, complaint, urgency) {
  state.selectedSlot = { slotId, slotTimeFormatted };
  state.selectedDoctor = { docId, docName };
  state.chiefComplaint = complaint || 'General Consultation';
  state.urgencyLevel = urgency || 'ROUTINE';

  if (!state.patientId) {
    try {
      const demoPat = await api.getDemoPatient();
      if (demoPat && demoPat.id) {
        state.patientId = demoPat.id;
        if (typeof voiceAssistant !== 'undefined' && voiceAssistant) {
          voiceAssistant.patientId = demoPat.id;
        }
      }
    } catch (_) {}
  }

  updateWorkflowStep(10); // Step 10: Patient selects slot

  // Populate booking modal
  document.getElementById('modal-doctor-name').innerText = docName;
  document.getElementById('modal-slot-time').innerText = slotTimeFormatted;
  document.getElementById('modal-complaint').innerText = state.chiefComplaint;
  document.getElementById('modal-patient-name').innerText = "Alice Morgan";

  // Reset sync progress box
  const progressBox = document.getElementById('modal-sync-progress');
  progressBox.innerHTML = '';
  document.getElementById('btn-confirm-booking').disabled = false;
  document.getElementById('btn-confirm-booking').innerText = 'Confirm & Synchronize with EHR';

  document.getElementById('booking-modal').classList.add('active');
};

// ----------------- RESILIENT BOOKING & EHR SYNC EXECUTION -----------------
window.executeBooking = async function() {
  const btn = document.getElementById('btn-confirm-booking');
  btn.disabled = true;
  btn.innerText = 'Synchronizing...';

  const progressBox = document.getElementById('modal-sync-progress');
  progressBox.innerHTML = `
    <div class="sync-step-row" id="sync-step-1">
      <span class="sync-icon-spin">⏳</span>
      <span>Step 11: Locking slot & creating provisional appointment record...</span>
    </div>
  `;
  updateWorkflowStep(11); // Step 11: Appointment creation

  const idempotencyKey = 'IDEM-' + Math.random().toString(36).substring(2, 11).toUpperCase();

  try {
    if (!state.patientId) {
      try {
        const demoPat = await api.getDemoPatient();
        if (demoPat && demoPat.id) state.patientId = demoPat.id;
      } catch (_) {}
    }

    // Artificial small visual pause for demo clarity
    await new Promise(r => setTimeout(r, 600));

    progressBox.innerHTML += `
      <div class="sync-step-row" id="sync-step-2">
        <span class="sync-icon-spin">⚡</span>
        <span>Step 12: Dispatching POST /ehr/appointments to Mock EHR (Chaos: ${state.chaosSetting ? state.chaosSetting.mode : 'NORMAL'})...</span>
      </div>
    `;
    updateWorkflowStep(12); // Step 12: Mock EHR

    const result = await api.confirmAppointment(
      state.selectedSlot ? state.selectedSlot.slotId : slotId,
      state.patientId,
      state.chiefComplaint || 'General Consultation',
      state.urgencyLevel || 'ROUTINE',
      `Triage: ${state.chiefComplaint || 'General Consultation'}. AI auto-mapped.`,
      idempotencyKey
    );

    state.activeAppointment = result;

    // Check if recovery scenario was triggered
    const recoveryLog = (result.sync_logs || []).find(l => l.action === 'VERIFY_RECOVERY');
    const timeoutLog = (result.sync_logs || []).find(l => l.status === 'TIMEOUT');

    if (timeoutLog && recoveryLog) {
      // VISUALIZE THE TIMEOUT AND IDEMPOTENT RECOVERY!
      progressBox.innerHTML += `
        <div class="sync-step-row" style="color: var(--accent-rose);">
          <span>⚠️</span>
          <span><strong>TIMEOUT ENCOUNTERED</strong>: Mock EHR socket hung up or timed out!</span>
        </div>
        <div class="sync-step-row" style="color: var(--accent-amber);">
          <span class="sync-icon-spin">🔍</span>
          <span>Step 13: Executing External Verification Query (GET /mock-ehr/verify?idempotency_key=${idempotencyKey})...</span>
        </div>
      `;
      updateWorkflowStep(13); // Step 13: External verification

      await new Promise(r => setTimeout(r, 800));

      progressBox.innerHTML += `
        <div class="sync-step-row" style="color: var(--accent-emerald);">
          <span>✓</span>
          <span>Step 14: External Verification SUCCEEDED! Record found in EHR (Ref: ${result.ehr_appointment_id}). Zero duplicates created!</span>
        </div>
      `;
      updateWorkflowStep(14); // Step 14: Internal synchronization
    } else {
      progressBox.innerHTML += `
        <div class="sync-step-row" style="color: var(--accent-emerald);">
          <span>✓</span>
          <span>Step 14: Internal synchronization completed. Ref: ${result.ehr_appointment_id}</span>
        </div>
      `;
      updateWorkflowStep(14);
    }

    await new Promise(r => setTimeout(r, 700));
    updateWorkflowStep(15); // Step 15: Patient confirmation

    // Close booking modal and open Pre-visit Questionnaire
    document.getElementById('booking-modal').classList.remove('active');
    state.selectedSlot = null;
    state.selectedDoctor = null;
    loadPatientDashboard();
    openQuestionnaireModal(result.id);

  } catch (err) {
    progressBox.innerHTML += `
      <div class="sync-step-row" style="color: var(--accent-rose);">
        <span>❌</span>
        <span>Synchronization Failed: ${err.message}</span>
      </div>
    `;
    btn.disabled = false;
    btn.innerText = 'Retry Booking';
  }
};

// ----------------- PRE-VISIT QUESTIONNAIRE FLOW -----------------
async function openQuestionnaireModal(appointmentId) {
  updateWorkflowStep(16); // Step 16: Pre-visit questionnaire

  try {
    const quest = await api.getQuestionnaire(appointmentId);
    state.activeQuestionnaire = quest;
    const formContainer = document.getElementById('questionnaire-form-container');
    
    let qHtml = '';
    (quest.questions || []).forEach((q, idx) => {
      const qType = (q.type || 'short_text').toLowerCase();
      const isReq = q.required !== false;
      const reqMarker = isReq ? '<span style="color: var(--accent-rose);">*</span>' : '<span style="color: var(--text-dim); font-size: 0.75rem;">(optional)</span>';

      if (qType === 'yes_no') {
        qHtml += `
          <div class="question-item" style="margin-bottom: 1.25rem; background: rgba(15, 23, 42, 0.5); padding: 1rem; border-radius: 8px; border: 1px solid rgba(255,255,255,0.06);">
            <label class="question-label" style="font-weight: 600; display: block; margin-bottom: 0.5rem; color: #f8fafc;">
              ${idx + 1}. ${q.question} ${reqMarker}
            </label>
            <div style="display: flex; gap: 1.5rem;">
              <label style="display: flex; align-items: center; gap: 0.4rem; cursor: pointer; color: var(--text-main);">
                <input type="radio" name="${q.id}" value="Yes" ${isReq ? 'required' : ''}> Yes
              </label>
              <label style="display: flex; align-items: center; gap: 0.4rem; cursor: pointer; color: var(--text-main);">
                <input type="radio" name="${q.id}" value="No" ${isReq ? 'required' : ''}> No
              </label>
            </div>
          </div>
        `;
      } else if (qType === 'choice') {
        const options = (q.options || []).map(opt => `
          <label style="display: flex; align-items: center; gap: 0.5rem; margin-bottom: 0.35rem; color: var(--text-muted); cursor: pointer;">
            <input type="radio" name="${q.id}" value="${opt}" ${isReq ? 'required' : ''}> ${opt}
          </label>
        `).join('');
        qHtml += `
          <div class="question-item" style="margin-bottom: 1.25rem; background: rgba(15, 23, 42, 0.5); padding: 1rem; border-radius: 8px; border: 1px solid rgba(255,255,255,0.06);">
            <label class="question-label" style="font-weight: 600; display: block; margin-bottom: 0.5rem; color: #f8fafc;">
              ${idx + 1}. ${q.question} ${reqMarker}
            </label>
            <div style="margin-top: 0.3rem;">${options}</div>
          </div>
        `;
      } else if (qType === 'multiple_choice') {
        const options = (q.options || []).map(opt => `
          <label style="display: flex; align-items: center; gap: 0.5rem; margin-bottom: 0.35rem; color: var(--text-muted); cursor: pointer;">
            <input type="checkbox" name="${q.id}[]" value="${opt}"> ${opt}
          </label>
        `).join('');
        qHtml += `
          <div class="question-item" style="margin-bottom: 1.25rem; background: rgba(15, 23, 42, 0.5); padding: 1rem; border-radius: 8px; border: 1px solid rgba(255,255,255,0.06);">
            <label class="question-label" style="font-weight: 600; display: block; margin-bottom: 0.5rem; color: #f8fafc;">
              ${idx + 1}. ${q.question} ${reqMarker}
            </label>
            <div style="margin-top: 0.3rem;">${options}</div>
          </div>
        `;
      } else if (qType === 'numeric' || qType === 'scale') {
        const minVal = q.min !== undefined ? q.min : 1;
        const maxVal = q.max !== undefined ? q.max : 10;
        qHtml += `
          <div class="question-item" style="margin-bottom: 1.25rem; background: rgba(15, 23, 42, 0.5); padding: 1rem; border-radius: 8px; border: 1px solid rgba(255,255,255,0.06);">
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 0.5rem;">
              <label class="question-label" style="font-weight: 600; color: #f8fafc;">${idx + 1}. ${q.question} ${reqMarker}</label>
              <span id="output-${q.id}" style="color: var(--accent-cyan); font-weight: 700; font-size: 1.1rem;">5</span>
            </div>
            <input type="range" name="${q.id}" min="${minVal}" max="${maxVal}" value="5" oninput="document.getElementById('output-${q.id}').innerText = this.value" style="width: 100%;">
            <div style="display: flex; justify-content: space-between; font-size: 0.75rem; color: var(--text-dim); margin-top: 0.2rem;">
              <span>Mild (${minVal})</span>
              <span style="color: #fda4af;">Severe (${maxVal})</span>
            </div>
          </div>
        `;
      } else if (qType === 'date') {
        qHtml += `
          <div class="question-item" style="margin-bottom: 1.25rem; background: rgba(15, 23, 42, 0.5); padding: 1rem; border-radius: 8px; border: 1px solid rgba(255,255,255,0.06);">
            <label class="question-label" style="font-weight: 600; display: block; margin-bottom: 0.5rem; color: #f8fafc;">
              ${idx + 1}. ${q.question} ${reqMarker}
            </label>
            <input type="date" name="${q.id}" class="custom-input" ${isReq ? 'required' : ''}>
          </div>
        `;
      } else if (qType === 'long_text') {
        qHtml += `
          <div class="question-item" style="margin-bottom: 1.25rem; background: rgba(15, 23, 42, 0.5); padding: 1rem; border-radius: 8px; border: 1px solid rgba(255,255,255,0.06);">
            <label class="question-label" style="font-weight: 600; display: block; margin-bottom: 0.5rem; color: #f8fafc;">
              ${idx + 1}. ${q.question} ${reqMarker}
            </label>
            <textarea name="${q.id}" class="custom-input" rows="3" placeholder="${q.placeholder || 'Please provide details...'}" ${isReq ? 'required' : ''}></textarea>
          </div>
        `;
      } else if (qType === 'structured_fields') {
        const subfields = (q.fields || []).map(sf => `
          <div style="margin-bottom: 0.5rem;">
            <label style="font-size: 0.8rem; color: var(--text-muted); display: block; margin-bottom: 0.2rem;">${sf.label || sf.id}</label>
            <input type="text" name="${q.id}__${sf.id}" class="custom-input" placeholder="${sf.placeholder || ''}">
          </div>
        `).join('');
        qHtml += `
          <div class="question-item" style="margin-bottom: 1.25rem; background: rgba(15, 23, 42, 0.5); padding: 1rem; border-radius: 8px; border: 1px solid rgba(255,255,255,0.06);">
            <label class="question-label" style="font-weight: 600; display: block; margin-bottom: 0.5rem; color: #f8fafc;">
              ${idx + 1}. ${q.question} ${reqMarker}
            </label>
            <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 0.75rem; margin-top: 0.4rem;">
              ${subfields}
            </div>
          </div>
        `;
      } else {
        // short_text / fallback
        qHtml += `
          <div class="question-item" style="margin-bottom: 1.25rem; background: rgba(15, 23, 42, 0.5); padding: 1rem; border-radius: 8px; border: 1px solid rgba(255,255,255,0.06);">
            <label class="question-label" style="font-weight: 600; display: block; margin-bottom: 0.5rem; color: #f8fafc;">
              ${idx + 1}. ${q.question} ${reqMarker}
            </label>
            <input type="text" name="${q.id}" class="custom-input" placeholder="${q.placeholder || 'Your response...'}" ${isReq ? 'required' : ''}>
          </div>
        `;
      }
    });

    formContainer.innerHTML = qHtml;
    document.getElementById('questionnaire-modal').classList.add('active');

  } catch (err) {
    console.error("Error loading questionnaire:", err);
  }
}

window.submitQuestionnaire = async function(event) {
  event.preventDefault();
  const form = document.getElementById('questionnaire-form');
  const formData = new FormData(form);
  const answers = {};

  for (const [key, value] of formData.entries()) {
    if (key.includes('__')) {
      // Structured subfield
      const [parentKey, subKey] = key.split('__');
      if (!answers[parentKey]) answers[parentKey] = {};
      answers[parentKey][subKey] = value;
    } else if (key.endsWith('[]')) {
      // Multiple choice array
      const cleanKey = key.slice(0, -2);
      if (!answers[cleanKey]) answers[cleanKey] = [];
      answers[cleanKey].push(value);
    } else {
      answers[key] = value;
    }
  }

  const btn = document.getElementById('btn-submit-quest');
  btn.disabled = true;
  btn.innerText = 'Evaluating & Submitting Intake...';

  try {
    const updated = await api.submitQuestionnaire(state.activeAppointment.id, answers);
    updateWorkflowStep(17); // Step 17: Workflow/reminder

    document.getElementById('questionnaire-modal').classList.remove('active');
    
    // Check if urgent clinical escalation was triggered
    if (updated.is_urgent) {
      const reasonsList = (updated.urgent_reasons || []).join('\n• ');
      alert(`⚠️ URGENT CLINICAL NOTICE:\n\nBased on your reported symptoms:\n• ${reasonsList}\n\nThis questionnaire has been marked as URGENT ESCALATION for your doctor.\nIf you are in acute distress or experiencing difficulty breathing/chest pain, call 911 or visit the emergency room immediately.`);
    } else {
      alert(`🎉 Pre-Visit Intake Completed!\n\nReference ID: ${state.activeAppointment.ehr_appointment_id}\nAutomated reminders have been scheduled.\nYour doctor will review your intake facts prior to your visit.`);
    }

    // Switch view to Doctor Queue so user can inspect doctor view immediately!
    switchView('doctor');
    updateWorkflowStep(18); // Step 18: Doctor view

  } catch (err) {
    alert("Error submitting questionnaire: " + err.message);
    btn.disabled = false;
    btn.innerText = 'Submit Intake & View Doctor Summary';
  }
};

// =========================================================================
// 1. PERSONA SWITCHING & AUTHENTICATION (PRD Section 4 & 21)
// =========================================================================

window.switchPersona = async function(roleName, facilityId = null, docId = null, shouldSwitchView = true) {
  state.currentPersona = roleName;

  // Update active pill button in Persona Switcher
  document.querySelectorAll('.persona-btn').forEach(btn => {
    btn.classList.toggle('active', btn.id === `btn-persona-${roleName.toLowerCase().replace('_', '-')}`);
  });

  const identityText = document.getElementById('identity-text');

  let email = 'alice.morgan@example.com';
  let pass = 'PatientPass123!';

  if (roleName === 'PLATFORM_ADMIN') {
    email = 'platform.admin@aegiscare.io';
    pass = 'PlatformAdmin123!';
  } else if (roleName === 'HOSPITAL_ADMIN') {
    if (facilityId && state.hospitalBId && facilityId === state.hospitalBId) {
      email = 'claire.vance@metrogeneral.org';
      pass = 'AdminPass123!';
    } else {
      email = 'david.miller@stjudehealth.org';
      pass = 'AdminPass123!';
    }
  } else if (roleName === 'DOCTOR') {
    if (docId && state.doctorBId && docId === state.doctorBId) {
      email = 'marcus.vance@metrogeneral.org';
      pass = 'DoctorPass123!';
    } else if (docId && (docId === 'fbaa5d82-c8fe-427b-9cac-03aec43a9cc3' || docId.startsWith('fbaa5d82'))) {
      email = 'arthur.pendelton@stjudehealth.org';
      pass = 'DoctorPass123!';
    } else {
      email = 'sarah.jenkins@stjudehealth.org';
      pass = 'DoctorPass123!';
    }
  } else {
    email = 'alice.morgan@example.com';
    pass = 'PatientPass123!';
  }

  try {
    const authRes = await api.login(email, pass);
    if (identityText) {
      identityText.innerText = `Authenticated: ${authRes.email || (authRes.user && authRes.user.email)} [${authRes.role || (authRes.user && authRes.user.role)}]`;
    }
    if (authRes.patient_id) {
      state.patientId = authRes.patient_id;
      if (typeof voiceAssistant !== 'undefined' && voiceAssistant) {
        voiceAssistant.patientId = authRes.patient_id;
      }
    }

    if (shouldSwitchView) {
      if (roleName === 'PLATFORM_ADMIN') {
        await switchView('admin', false);
      } else if (roleName === 'HOSPITAL_ADMIN') {
        await switchView('hospital-admin', false);
      } else if (roleName === 'DOCTOR') {
        await switchView('doctor', false);
      } else {
        await switchView('patient', false);
      }
    }
  } catch (err) {
    console.error("Error switching persona:", err);
    alert(`Could not authenticate persona ${roleName}: ${err.message}`);
  }
};

// =========================================================================
// 2. PATIENT DASHBOARD (7 SECTIONS)
// =========================================================================

async function loadPatientDashboard() {
  const upcomingContainer = document.getElementById('patient-upcoming-appts-list');
  const questContainer = document.getElementById('patient-questionnaires-list');

  // Fallback auto-auth if token is missing
  if (!api.getToken()) {
    try {
      await api.login('alice.morgan@example.com', 'PatientPass123!');
    } catch (_) {}
  }

  try {
    const data = await api.getPatientDashboard();

    // Cache active patient ID for ownership-scoped profile and appointment actions
    if (data.profile_management) {
      state.patientId = data.profile_management.id || data.profile_management.patient_id || state.patientId;
      if (typeof voiceAssistant !== 'undefined' && voiceAssistant) {
        voiceAssistant.patientId = state.patientId;
      }
    }
    
    // 1. Home Welcome & Status
    const welcomeEl = document.getElementById('patient-welcome-name');
    const hospEl = document.getElementById('patient-primary-hosp-name');
    const nextVisitEl = document.getElementById('patient-next-visit-text');
    const pendingIntakeEl = document.getElementById('patient-pending-intake-badge');

    if (welcomeEl && data.home) welcomeEl.innerText = data.home.greeting || "Welcome back, Patient";
    if (hospEl && data.home) hospEl.innerText = data.home.primary_hospital_name || "AegisCare Health Network";
    if (nextVisitEl && data.home && data.home.next_visit_summary) {
      const n = data.home.next_visit_summary;
      nextVisitEl.innerText = `${n.doctor_name || 'Specialist'} (${n.specialty || 'General'})`;
    }
    if (pendingIntakeEl && data.home) {
      const count = data.home.pending_questionnaires_count || 0;
      pendingIntakeEl.innerText = `📋 ${count} Pre-Visit Intake${count === 1 ? '' : 's'} Pending`;
      pendingIntakeEl.style.display = count > 0 ? 'inline-block' : 'none';
    }

    // 2. Upcoming Consultations with Reschedule & Cancel actions
    const upcomingCount = document.getElementById('patient-upcoming-count');
    const upcoming = data.upcoming_appointments || [];
    if (upcomingCount) upcomingCount.innerText = `${upcoming.length} Scheduled`;

    if (upcomingContainer) {
      if (upcoming.length === 0) {
        upcomingContainer.innerHTML = '<div style="color: var(--text-dim); text-align: center; padding: 1.5rem;">No upcoming appointments. Speak to the voice concierge above to book one!</div>';
      } else {
        upcomingContainer.innerHTML = upcoming.map(a => {
          const formattedDate = a.slot_time && a.slot_time !== 'None' ? new Date(a.slot_time).toLocaleString() : 'Today / Scheduled';
          const safeDocName = (a.doctor_name || 'Specialist').replace(/'/g, "\\'");
          const safeSlotTime = (a.slot_time || formattedDate).replace(/'/g, "\\'");
          return `
            <div style="background: rgba(255,255,255,0.03); border: 1px solid rgba(255,255,255,0.08); border-radius: 8px; padding: 0.85rem; margin-bottom: 0.6rem;">
              <div style="display: flex; justify-content: space-between; align-items: flex-start; flex-wrap: wrap; gap: 0.5rem;">
                <div>
                  <div style="font-weight: 600; color: #f8fafc;">${a.doctor_name} <span style="font-size: 0.8rem; color: #38bdf8;">(${a.specialty})</span></div>
                  <div style="font-size: 0.8rem; color: var(--text-muted); margin-top: 0.2rem;">
                    📅 ${formattedDate} • <span style="color: var(--accent-emerald);">EHR Ref: ${a.ehr_appointment_id || 'Internal'}</span>
                  </div>
                  <div style="font-size: 0.8rem; color: var(--text-dim); margin-top: 0.15rem;">Complaint: ${a.chief_complaint || 'General Checkup'}</div>
                </div>
                <span class="status-pill ${a.status.toLowerCase()}">${a.status}</span>
              </div>
              ${a.status !== 'CANCELLED' ? `
                <div style="display: flex; justify-content: flex-end; gap: 0.5rem; margin-top: 0.6rem; border-top: 1px solid rgba(255,255,255,0.05); padding-top: 0.5rem;">
                  <button class="btn-secondary" style="padding: 0.25rem 0.65rem; font-size: 0.76rem;" onclick="openRescheduleModal('${a.id}', '${a.doctor_id || ''}', '${safeDocName}', '${safeSlotTime}')">
                    🔄 Reschedule
                  </button>
                  <button class="btn-secondary" style="padding: 0.25rem 0.65rem; font-size: 0.76rem; color: #f43f5e; border-color: rgba(244,63,94,0.3);" onclick="openCancelModal('${a.id}', '${safeDocName}', '${safeSlotTime}')">
                    ❌ Cancel
                  </button>
                </div>
              ` : ''}
            </div>
          `;
        }).join('');
      }
    }

    // 3. Pre-Visit Intake Questionnaires
    const quests = data.questionnaires || [];
    if (questContainer) {
      if (quests.length === 0) {
        questContainer.innerHTML = '<div style="color: var(--text-dim); text-align: center; padding: 1.5rem;">All pre-visit intake questionnaires completed!</div>';
      } else {
        questContainer.innerHTML = quests.map(q => `
          <div style="background: rgba(255,255,255,0.03); border: 1px solid rgba(255,255,255,0.08); border-radius: 8px; padding: 0.85rem; display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 0.5rem; margin-bottom: 0.5rem;">
            <div>
              <div style="font-weight: 600; color: #f8fafc;">${q.title || 'Pre-Visit Clinical Intake'}</div>
              <div style="font-size: 0.8rem; color: var(--text-muted); margin-top: 0.2rem;">
                Status: <span class="status-pill ${q.status.toLowerCase()}">${q.status}</span> • ${q.questions_count || 4} questions
              </div>
            </div>
            ${q.status !== 'COMPLETED' && q.status !== 'REVIEWED' ? `
              <button class="btn-primary" style="padding: 0.35rem 0.8rem; font-size: 0.8rem;" onclick="openQuestionnaireModal('${q.appointment_id}')">
                Fill Out Intake
              </button>
            ` : '<span style="color: var(--accent-emerald); font-size: 0.85rem;">✓ Submitted</span>'}
          </div>
        `).join('');
      }
    }

    // 4. Historical Consultations (With Cancellation Reason support)
    const historyContainer = document.getElementById('patient-history-appts-list');
    const history = data.historical_appointments || [];
    if (historyContainer) {
      if (history.length === 0) {
        historyContainer.innerHTML = '<div style="color: var(--text-dim); font-size: 0.85rem; padding: 0.5rem 0;">No past consultations recorded.</div>';
      } else {
        historyContainer.innerHTML = history.map(h => `
          <div style="background: rgba(255,255,255,0.02); border: 1px solid rgba(255,255,255,0.05); border-radius: 6px; padding: 0.6rem; font-size: 0.82rem; margin-bottom: 0.35rem;">
            <div style="display: flex; justify-content: space-between; align-items: center;">
              <div style="font-weight: 600; color: #e2e8f0;">${h.doctor_name} (${h.specialty})</div>
              <span class="status-pill ${h.status.toLowerCase()}">${h.status}</span>
            </div>
            <div style="color: var(--text-muted); font-size: 0.75rem; margin-top: 0.2rem;">
              ${h.slot_time && h.slot_time !== 'None' ? new Date(h.slot_time).toLocaleDateString() : 'Past'} • ${h.chief_complaint || 'Completed'}
            </div>
            ${h.cancellation_reason ? `
              <div style="color: #f43f5e; font-size: 0.73rem; margin-top: 0.2rem;">
                Reason: ${h.cancellation_reason}
              </div>
            ` : ''}
          </div>
        `).join('');
      }
    }

    // 5. Preferences (Data Minimization: Channel, Language, Accessibility)
    const commSelect = document.getElementById('pref-comm-channel');
    const langSelect = document.getElementById('pref-language');
    const accessSelect = document.getElementById('pref-accessibility');
    if (data.preferences) {
      if (commSelect) commSelect.value = data.preferences.communication_preference || 'SMS';
      const prefs = data.preferences.preferences || {};
      if (langSelect && prefs.language) langSelect.value = prefs.language;
      if (accessSelect && prefs.accessibility) accessSelect.value = prefs.accessibility;
    }

    // 6. Profile Demographics & External ID
    const nameInput = document.getElementById('profile-name-input');
    const phoneInput = document.getElementById('profile-phone-input');
    const dobInput = document.getElementById('profile-dob-input');
    const genderSelect = document.getElementById('profile-gender-input');
    const extIdInput = document.getElementById('profile-extid-input');
    const emergInput = document.getElementById('profile-emergency-input');
    const mrnBadge = document.getElementById('profile-mrn-badge');

    if (data.profile_management) {
      const p = data.profile_management;
      if (nameInput) nameInput.value = p.full_name || '';
      if (phoneInput) phoneInput.value = p.phone || '';
      if (dobInput) dobInput.value = p.date_of_birth || '';
      if (genderSelect && p.gender) genderSelect.value = p.gender;
      if (extIdInput) extIdInput.value = p.external_patient_id || '';
      if (emergInput) emergInput.value = p.emergency_contact || '';
      if (mrnBadge) mrnBadge.innerText = `MRN: ${p.patient_mrn || 'Assigned'}`;
    }

  } catch (err) {
    console.error("Error loading patient dashboard:", err);
    if (upcomingContainer) {
      upcomingContainer.innerHTML = `
        <div style="color: var(--text-dim); text-align: center; padding: 1.5rem;">
          <div>Could not load scheduled visits: ${err.message}</div>
          <button class="btn-primary" style="margin-top: 0.5rem; padding: 0.35rem 0.8rem; font-size: 0.8rem;" onclick="switchPersona('PATIENT')">
            👤 Reconnect Patient Portal
          </button>
        </div>
      `;
    }
  }
}
window.loadPatientDashboard = loadPatientDashboard;

// Preferences Update Handler
window.handleSavePatientPreferences = async function(event) {
  event.preventDefault();
  const chan = document.getElementById('pref-comm-channel').value;
  const lang = document.getElementById('pref-language') ? document.getElementById('pref-language').value : 'en';
  const access = document.getElementById('pref-accessibility') ? document.getElementById('pref-accessibility').value : 'standard';
  try {
    await api.updatePatientPreferences({
      communication_preference: chan,
      preferences: { language: lang, accessibility: access }
    });
    alert(`✓ Preferences updated! Notifications routed via ${chan}.`);
    loadPatientDashboard();
  } catch (err) {
    alert("Error updating preferences: " + err.message);
  }
};

// Profile Update Handler (Full demographics + external patient ID)
window.handleUpdatePatientProfile = async function(event) {
  event.preventDefault();
  const name = document.getElementById('profile-name-input').value.trim();
  const phone = document.getElementById('profile-phone-input').value.trim();
  const dob = document.getElementById('profile-dob-input').value || null;
  const gender = document.getElementById('profile-gender-input').value;
  const extId = document.getElementById('profile-extid-input').value.trim();
  const emergency = document.getElementById('profile-emergency-input').value.trim();

  try {
    if (state.patientId) {
      await api.updatePatientProfile(state.patientId, {
        name,
        phone,
        date_of_birth: dob,
        gender,
        emergency_contact: emergency || null,
        external_patient_id: extId || null
      });
    } else {
      await api.updatePatientPreferences({
        phone,
        emergency_contact: emergency
      });
    }
    alert("✓ Patient profile updated successfully.");
    loadPatientDashboard();
  } catch (err) {
    alert("Error updating profile: " + err.message);
  }
};

// ----------------- PATIENT AUTH & REGISTRATION MODAL FLOWS -----------------
window.openPatientAuthModal = function() {
  const modal = document.getElementById('patient-auth-modal');
  if (modal) modal.classList.add('active');
};
window.closePatientAuthModal = function() {
  const modal = document.getElementById('patient-auth-modal');
  if (modal) modal.classList.remove('active');
};

window.switchAuthTab = function(tab) {
  const regForm = document.getElementById('patient-register-form');
  const loginForm = document.getElementById('patient-login-form');
  const regTab = document.getElementById('tab-auth-register');
  const loginTab = document.getElementById('tab-auth-login');
  const title = document.getElementById('patient-auth-title');

  if (tab === 'register') {
    if (regForm) regForm.style.display = 'block';
    if (loginForm) loginForm.style.display = 'none';
    if (regTab) regTab.className = 'btn-primary';
    if (loginTab) loginTab.className = 'btn-secondary';
    if (title) title.innerText = 'New Patient Registration';
  } else {
    if (regForm) regForm.style.display = 'none';
    if (loginForm) loginForm.style.display = 'block';
    if (regTab) regTab.className = 'btn-secondary';
    if (loginTab) loginTab.className = 'btn-primary';
    if (title) title.innerText = 'Existing Patient Login';
  }
};

window.handlePatientRegisterSubmit = async function(event) {
  event.preventDefault();
  const name = document.getElementById('patient-reg-name').value.trim();
  const email = document.getElementById('patient-reg-email').value.trim();
  const phone = document.getElementById('patient-reg-phone').value.trim();
  const password = document.getElementById('patient-reg-password').value;
  const dob = document.getElementById('patient-reg-dob').value || null;
  const gender = document.getElementById('patient-reg-gender').value;
  const emergency = document.getElementById('patient-reg-emergency').value.trim();
  const comm = document.getElementById('patient-reg-comm').value;
  const extId = document.getElementById('patient-reg-extid').value.trim();
  const btn = document.getElementById('btn-submit-patient-reg');

  btn.disabled = true;
  btn.innerText = 'Creating Account...';

  let regRes;
  try {
    regRes = await api.registerPatient({
      full_name: name,
      email,
      phone,
      password,
      date_of_birth: dob,
      gender,
      emergency_contact: emergency || null,
      communication_preference: comm,
      external_patient_id: extId || null
    });
  } catch (err) {
    console.error("Patient registration error:", err);
    alert(`Registration failed: ${err.message}`);
    btn.disabled = false;
    btn.innerText = 'Create Account & Log In';
    return;
  }

  // Attempt auto-login with newly registered credentials
  try {
    const authRes = await api.login(email, password);
    if (authRes.patient_id) {
      state.patientId = authRes.patient_id;
      if (typeof voiceAssistant !== 'undefined' && voiceAssistant) {
        voiceAssistant.patientId = authRes.patient_id;
      }
    }
    const identityText = document.getElementById('identity-text');
    if (identityText) {
      identityText.innerText = `Authenticated: ${authRes.email || email} [PATIENT]`;
    }

    closePatientAuthModal();
    alert(`✓ Welcome, ${name}! Your patient account is ready (MRN: ${regRes.patient_mrn || 'Assigned'}).`);
    await loadPatientDashboard();
  } catch (loginErr) {
    console.warn("Auto-login after registration notice:", loginErr);
    closePatientAuthModal();
    alert(`✓ Account created successfully (MRN: ${regRes.patient_mrn || 'Assigned'})! However, automatic sign-in failed: ${loginErr.message}. Please sign in using the Login tab.`);
  } finally {
    btn.disabled = false;
    btn.innerText = 'Create Account & Log In';
  }
};

window.handlePatientLoginSubmit = async function(event) {
  event.preventDefault();
  const email = document.getElementById('patient-login-email').value.trim();
  const password = document.getElementById('patient-login-password').value;
  const btn = document.getElementById('btn-submit-patient-login');

  btn.disabled = true;
  btn.innerText = 'Authenticating...';

  try {
    const authRes = await api.login(email, password);
    if (authRes.patient_id) {
      state.patientId = authRes.patient_id;
      if (typeof voiceAssistant !== 'undefined' && voiceAssistant) {
        voiceAssistant.patientId = authRes.patient_id;
      }
    }
    const identityText = document.getElementById('identity-text');
    if (identityText) {
      identityText.innerText = `Authenticated: ${authRes.email || email} [${authRes.role || 'PATIENT'}]`;
    }

    closePatientAuthModal();
    alert(`✓ Logged in as ${email}`);
    await loadPatientDashboard();
  } catch (err) {
    console.error("Patient login error:", err);
    alert(`Login failed: ${err.message}`);
  } finally {
    btn.disabled = false;
    btn.innerText = 'Log In to Dashboard';
  }
};

// ----------------- APPOINTMENT RESCHEDULING & CANCELLATION MODALS -----------------
window.openRescheduleModal = async function(appointmentId, doctorId, doctorName, slotTime) {
  const modal = document.getElementById('reschedule-modal');
  if (!modal) return;

  document.getElementById('reschedule-appointment-id').value = appointmentId;
  document.getElementById('reschedule-doctor-name').innerText = doctorName || 'Physician';
  document.getElementById('reschedule-current-time').innerText = slotTime && slotTime !== 'None' ? new Date(slotTime).toLocaleString() : 'Scheduled';
  
  const slotSelect = document.getElementById('reschedule-slot-select');
  slotSelect.innerHTML = '<option value="">Loading available slots...</option>';
  modal.classList.add('active');

  try {
    let slots = [];
    if (doctorId && doctorId !== 'None' && doctorId !== 'null' && doctorId !== 'undefined') {
      slots = await api.getDoctorSlots(doctorId);
    } else {
      const doctors = await api.getDoctors();
      if (doctors && doctors.length > 0) {
        slots = await api.getDoctorSlots(doctors[0].id);
      }
    }

    const available = (slots || []).filter(s => s.status === 'AVAILABLE');
    if (available.length === 0) {
      slotSelect.innerHTML = '<option value="">No available future slots found for this doctor.</option>';
    } else {
      slotSelect.innerHTML = '<option value="">-- Choose New Slot --</option>' + available.map(s => {
        const timeFormatted = new Date(s.start_time).toLocaleString(undefined, { weekday: 'short', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
        return `<option value="${s.id}">${timeFormatted} (Available)</option>`;
      }).join('');
    }
  } catch (err) {
    console.error("Error loading doctor slots for reschedule:", err);
    slotSelect.innerHTML = `<option value="">Error loading slots: ${err.message}</option>`;
  }
};

window.closeRescheduleModal = function() {
  const modal = document.getElementById('reschedule-modal');
  if (modal) modal.classList.remove('active');
};

window.executeReschedule = async function() {
  const apptId = document.getElementById('reschedule-appointment-id').value;
  const newSlotId = document.getElementById('reschedule-slot-select').value;
  const reason = document.getElementById('reschedule-reason-input').value.trim();
  const btn = document.getElementById('btn-confirm-reschedule');

  if (!newSlotId) {
    alert("Please select an available consultation slot.");
    return;
  }

  btn.disabled = true;
  btn.innerText = 'Rescheduling & Syncing EHR...';

  try {
    await api.rescheduleAppointment(apptId, newSlotId, reason || "Patient requested reschedule");
    alert("✓ Appointment rescheduled and synchronized with EHR successfully!");
    closeRescheduleModal();
    await loadPatientDashboard();
  } catch (err) {
    console.error("Reschedule error:", err);
    alert(`Failed to reschedule appointment: ${err.message}`);
  } finally {
    btn.disabled = false;
    btn.innerText = 'Confirm Reschedule & Sync EHR';
  }
};

window.openCancelModal = function(appointmentId, doctorName, slotTime) {
  const modal = document.getElementById('cancel-modal');
  if (!modal) return;

  document.getElementById('cancel-appointment-id').value = appointmentId;
  document.getElementById('cancel-doctor-name').innerText = doctorName || 'Physician';
  document.getElementById('cancel-slot-time').innerText = slotTime && slotTime !== 'None' ? new Date(slotTime).toLocaleString() : 'Scheduled';
  modal.classList.add('active');
};

window.closeCancelModal = function() {
  const modal = document.getElementById('cancel-modal');
  if (modal) modal.classList.remove('active');
};

window.executeCancellation = async function() {
  const apptId = document.getElementById('cancel-appointment-id').value;
  const reason = document.getElementById('cancel-reason-input').value.trim() || document.getElementById('cancel-reason-preset').value;
  const btn = document.getElementById('btn-confirm-cancel');

  btn.disabled = true;
  btn.innerText = 'Cancelling...';

  try {
    await api.cancelAppointment(apptId, reason || "Patient requested cancellation");
    alert("✓ Appointment cancelled and slot released successfully.");
    closeCancelModal();
    await loadPatientDashboard();
  } catch (err) {
    console.error("Cancellation error:", err);
    alert(`Failed to cancel appointment: ${err.message}`);
  } finally {
    btn.disabled = false;
    btn.innerText = 'Confirm Cancellation';
  }
};

// =========================================================================
// 3. DOCTOR DASHBOARD (8 SECTIONS & OWNERSHIP)
// =========================================================================

state.doctorSubTab = 'schedule';

window.switchDoctorSubTab = function(tab) {
  state.doctorSubTab = tab;
  document.querySelectorAll('#doctor-section .subtab-btn').forEach(btn => {
    btn.classList.toggle('active', btn.id === `tab-doc-${tab}`);
  });

  const views = {
    schedule: 'doctor-view-schedule',
    calendar: 'doctor-view-calendar',
    blocked: 'doctor-view-blocked',
    intake: 'doctor-view-intake',
    questionnaires: 'doctor-view-questionnaires'
  };

  Object.entries(views).forEach(([k, viewId]) => {
    const el = document.getElementById(viewId);
    if (el) el.style.display = k === tab ? 'block' : 'none';
  });

  if (state.activeDoctorId) {
    loadDoctorDashboard(state.activeDoctorId);
  }
};

async function loadDoctorDashboard(doctorId = null) {
  const select = document.getElementById('doctor-select');

  try {
    const doctors = await api.getDoctors();
    if (doctors && doctors.length > 0) {
      if (select && select.options.length === 0) {
        select.innerHTML = doctors.map(d => `<option value="${d.id}">${d.full_name} (${d.specialty})</option>`).join('');
      }
      if (!doctorId) {
        doctorId = select && select.value ? select.value : doctors[0].id;
      }
      state.activeDoctorId = doctorId;
      if (select) select.value = doctorId;
    }

    if (select) {
      select.onchange = async (e) => {
        state.activeDoctorId = e.target.value;
        await switchPersona('DOCTOR', null, state.activeDoctorId, false);
        loadDoctorDashboard(state.activeDoctorId);
      };
    }

    const data = await api.getDoctorDashboard(doctorId);

    // 1. Today's Consultations
    const todayContainer = document.getElementById('doctor-today-queue-container');
    const todayCountEl = document.getElementById('doc-today-count');
    const todays = data.today_appointments || data.todays_appointments || [];
    if (todayCountEl) todayCountEl.innerText = `${todays.length} Today`;

    if (todayContainer) {
      if (todays.length === 0) {
        todayContainer.innerHTML = '<div style="color: var(--text-dim); text-align: center; padding: 1.5rem;">No appointments scheduled for today.</div>';
      } else {
        todayContainer.innerHTML = todays.map(a => `
          <div style="background: rgba(255,255,255,0.03); border: 1px solid rgba(255,255,255,0.08); border-radius: 8px; padding: 0.85rem;">
            <div style="display: flex; justify-content: space-between; align-items: center;">
              <strong>${a.patient_name || 'Patient'}</strong>
              <span class="status-pill ${a.status.toLowerCase()}">${a.status}</span>
            </div>
            <div style="font-size: 0.8rem; color: var(--text-muted); margin-top: 0.25rem;">
              📅 ${a.slot_time ? new Date(a.slot_time).toLocaleTimeString() : 'N/A'} • Phone: ${a.patient_phone || '+1-555-0100'}
            </div>
            <div style="font-size: 0.8rem; color: #a5f3fc; margin-top: 0.25rem;">Complaint: ${a.chief_complaint || 'Routine checkup'}</div>
          </div>
        `).join('');
      }
    }

    // 2. Upcoming Consultations
    const upcomingContainer = document.getElementById('doctor-upcoming-queue-container');
    const upcoming = data.upcoming_appointments || [];
    if (upcomingContainer) {
      if (upcoming.length === 0) {
        upcomingContainer.innerHTML = '<div style="color: var(--text-dim); text-align: center; padding: 1.5rem;">No upcoming appointments booked.</div>';
      } else {
        upcomingContainer.innerHTML = upcoming.map(a => `
          <div style="background: rgba(255,255,255,0.03); border: 1px solid rgba(255,255,255,0.08); border-radius: 8px; padding: 0.85rem;">
            <div style="display: flex; justify-content: space-between; align-items: center;">
              <strong>${a.patient_name || 'Patient'}</strong>
              <span class="status-pill ${a.status.toLowerCase()}">${a.status}</span>
            </div>
            <div style="font-size: 0.8rem; color: var(--text-muted); margin-top: 0.25rem;">
              📅 ${a.slot_time ? new Date(a.slot_time).toLocaleString() : 'N/A'} • EHR Ref: <span style="color: #38bdf8;">${a.ehr_appointment_id || 'Pending'}</span>
            </div>
          </div>
        `).join('');
      }
    }

    // 3. Calendar & Availability
    const calList = document.getElementById('doc-calendars-list');
    const cal = data.calendar || {};
    if (calList) {
      calList.innerHTML = `
        <div style="background: rgba(255,255,255,0.03); border: 1px solid rgba(255,255,255,0.08); border-radius: 8px; padding: 1rem;">
          <div style="font-weight: 600; color: #38bdf8;">${cal.primary_calendar || 'Main Consultation Calendar'}</div>
          <div style="font-size: 0.85rem; color: var(--text-muted); margin-top: 0.3rem;">Timezone: ${cal.timezone || 'UTC'} • Configured Calendars: ${cal.calendars_count || 1}</div>
          <div style="margin-top: 0.5rem; font-size: 0.8rem; color: var(--accent-emerald);">✓ Synchronized with EHR Master Calendar</div>
        </div>
      `;
    }

    const availList = document.getElementById('doc-availability-list');
    const avails = data.availability || [];
    const days = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'];
    if (availList) {
      if (avails.length === 0) {
        availList.innerHTML = '<div style="color: var(--text-dim);">No recurring availability set.</div>';
      } else {
        availList.innerHTML = avails.map(av => `
          <div style="background: rgba(255,255,255,0.02); border: 1px solid rgba(255,255,255,0.06); border-radius: 6px; padding: 0.5rem 0.75rem; margin-bottom: 0.4rem; display: flex; justify-content: space-between; font-size: 0.85rem;">
            <span>${days[av.day_of_week] || 'Weekday'}</span>
            <span style="color: #38bdf8; font-weight: 600;">${av.start_time} - ${av.end_time} (${av.slot_duration_minutes || 30}m slots)</span>
          </div>
        `).join('');
      }
    }

    // 4. Blocked Time
    const blockedContainer = document.getElementById('doc-blocked-slots-container');
    const blockedCountEl = document.getElementById('doc-blocked-count');
    const blocked = data.blocked_time || [];
    if (blockedCountEl) blockedCountEl.innerText = `${blocked.length} Blocked`;

    if (blockedContainer) {
      if (blocked.length === 0) {
        blockedContainer.innerHTML = '<div style="color: var(--text-dim); text-align: center; padding: 1.5rem;">No blocked time windows. Calendar is open for booking.</div>';
      } else {
        blockedContainer.innerHTML = blocked.map(b => `
          <div style="background: rgba(244, 63, 94, 0.08); border: 1px solid rgba(244, 63, 94, 0.3); border-radius: 8px; padding: 0.85rem; display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 0.5rem;">
            <div>
              <strong style="color: #fda4af;">⛔ ${b.reason || 'Blocked Time'}</strong>
              <div style="font-size: 0.8rem; color: var(--text-muted); margin-top: 0.2rem;">
                ${b.start_time ? new Date(b.start_time).toLocaleString() : 'N/A'} - ${b.end_time ? new Date(b.end_time).toLocaleString() : 'N/A'}
              </div>
            </div>
            <button class="btn-secondary" style="padding: 0.3rem 0.7rem; font-size: 0.75rem; border-color: rgba(255,255,255,0.2);" onclick="handleUnblockDoctorSlot('${b.id}')">
              Unblock Slot
            </button>
          </div>
        `).join('');
      }
    }

    // 5. Authorized Pre-Visit Responses
    const intakeContainer = document.getElementById('doctor-intake-queue-container');
    const responses = data.authorized_pre_visit_responses || data.authorized_previsit_responses || [];
    const urgentCount = responses.filter(r => r.is_urgent && r.status !== 'REVIEWED').length;
    const badgeUrgent = document.getElementById('badge-doc-urgent');
    if (badgeUrgent) {
      badgeUrgent.style.display = urgentCount > 0 ? 'inline-block' : 'none';
      badgeUrgent.innerText = `${urgentCount} URGENT`;
    }

    if (intakeContainer) {
      if (responses.length === 0) {
        intakeContainer.innerHTML = '<div style="color: var(--text-dim); text-align: center; padding: 2rem;">No pre-visit questionnaire responses submitted yet for your patients.</div>';
      } else {
        intakeContainer.innerHTML = responses.map(r => {
          const isUrgent = r.is_urgent;
          const borderCol = isUrgent ? 'var(--accent-rose)' : (r.status === 'REVIEWED' ? 'var(--accent-emerald)' : 'var(--accent-cyan)');
          return `
            <div class="glass-panel" style="padding: 1.5rem; margin-bottom: 1rem; border-left: 4px solid ${borderCol};">
              <div style="display: flex; justify-content: space-between; align-items: flex-start; flex-wrap: wrap; gap: 0.5rem;">
                <div>
                  <div style="display: flex; align-items: center; gap: 0.6rem;">
                    <h3 style="margin: 0;">${r.patient_name || 'Patient'}</h3>
                    ${isUrgent ? '<span style="background: rgba(244, 63, 94, 0.2); color: #fda4af; border: 1px solid var(--accent-rose); padding: 0.15rem 0.5rem; border-radius: 999px; font-weight: 700; font-size: 0.75rem;">🚨 URGENT CLINICAL ESCALATION</span>' : ''}
                  </div>
                  <div style="color: var(--text-muted); font-size: 0.85rem; margin-top: 0.25rem;">
                    Status: <span class="status-pill ${r.status.toLowerCase()}">${r.status}</span>
                  </div>
                </div>
                ${r.status !== 'REVIEWED' ? `
                  <button class="btn-primary" style="padding: 0.35rem 0.8rem; font-size: 0.85rem;" onclick="openDoctorReviewModal('${r.appointment_id}')">
                    🔍 Review Intake & Sign Off
                  </button>
                ` : '<span style="color: var(--accent-emerald); font-weight: 600;">✓ Reviewed & Signed Off</span>'}
              </div>
              <div style="margin-top: 0.75rem; background: rgba(15, 23, 42, 0.6); padding: 0.85rem; border-radius: 6px;">
                <div style="font-size: 0.75rem; color: var(--text-dim); text-transform: uppercase; font-weight: 700;">Patient Intake Summary</div>
                <div style="margin-top: 0.3rem; color: #f1f5f9; font-size: 0.88rem;">${r.patient_intake_summary || 'Intake completed.'}</div>
              </div>
            </div>
          `;
        }).join('');
      }
    }

    // 6. Questionnaires Assigned
    const questContainer = document.getElementById('doctor-questionnaires-container');
    const doctorQuests = data.questionnaires || [];
    if (questContainer) {
      if (doctorQuests.length === 0) {
        questContainer.innerHTML = '<div style="color: var(--text-dim);">No approved questionnaires configured for this doctor or specialty.</div>';
      } else {
        questContainer.innerHTML = doctorQuests.map(q => `
          <div style="background: rgba(255,255,255,0.03); border: 1px solid rgba(255,255,255,0.08); border-radius: 8px; padding: 0.85rem; margin-bottom: 0.5rem; display: flex; justify-content: space-between; align-items: center;">
            <div>
              <div style="font-weight: 600; color: #f8fafc;">${q.title}</div>
              <div style="font-size: 0.8rem; color: var(--text-muted);">${q.condition_category || 'GENERAL_INTAKE'} • ${q.questions_count || 4} questions</div>
            </div>
            <span class="status-pill success">Approved</span>
          </div>
        `).join('');
      }
    }

  } catch (err) {
    console.error("Error loading doctor dashboard:", err);
  }
}
window.loadDoctorDashboard = loadDoctorDashboard;
window.loadDoctorView = () => loadDoctorDashboard(state.activeDoctorId);

window.handleBlockDoctorSlot = async function(event) {
  event.preventDefault();
  const form = event.target;
  const start = form.start_time.value;
  const end = form.end_time.value;
  const reason = form.reason.value;

  if (!state.activeDoctorId) {
    alert("Please select a doctor first.");
    return;
  }

  try {
    await api.createDoctorBlockedSlot({
      doctor_id: state.activeDoctorId,
      start_time: new Date(start).toISOString(),
      end_time: new Date(end).toISOString(),
      reason
    });
    alert("✓ Time window successfully blocked! AI and scheduling rules will respect this lock.");
    form.reset();
    loadDoctorDashboard(state.activeDoctorId);
  } catch (err) {
    alert("Error blocking slot: " + err.message);
  }
};

window.handleUnblockDoctorSlot = async function(slotId) {
  if (!confirm("Are you sure you want to unblock this time window?")) return;
  try {
    await api.deleteDoctorBlockedSlot(slotId);
    alert("✓ Time window unblocked. Slots returned to available pool.");
    loadDoctorDashboard(state.activeDoctorId);
  } catch (err) {
    alert("Error unblocking slot: " + err.message);
  }
};

// =========================================================================
// 4. HOSPITAL ADMIN DASHBOARD (12 SECTIONS & TENANT ISOLATION)
// =========================================================================

window.switchHospitalAdminSubTab = function(tab) {
  state.hospitalAdminSubTab = tab;
  document.querySelectorAll('#hospital-admin-section .subtab-btn').forEach(btn => {
    btn.classList.toggle('active', btn.id === `hosp-tab-btn-${tab}`);
  });

  const views = {
    overview: 'hosp-subtab-overview',
    appointments: 'hosp-subtab-appointments',
    doctors: 'hosp-subtab-doctors',
    intake: 'hosp-subtab-intake',
    integrations: 'hosp-subtab-integrations'
  };

  Object.entries(views).forEach(([k, viewId]) => {
    const el = document.getElementById(viewId);
    if (el) el.style.display = k === tab ? 'block' : 'none';
  });
};

async function loadHospitalAdminDashboard(hospitalId = null) {
  const select = document.getElementById('hospital-admin-select');

  try {
    const hospitals = await api.getHospitals();
    if (hospitals && hospitals.length > 0) {
      if (select && select.options.length === 0) {
        select.innerHTML = hospitals.map(h => `<option value="${h.id}">${h.name} (${h.license_number})</option>`).join('');
      }
      if (!hospitalId) {
        if (state.currentUser && state.currentUser.hospital_id) {
          hospitalId = state.currentUser.hospital_id;
        } else {
          hospitalId = select && select.value ? select.value : hospitals[0].id;
        }
      }
      state.activeHospitalId = hospitalId;
      if (select) select.value = hospitalId;
    }

    const data = await api.getHospitalAdminDashboard(hospitalId);

    // Header Title
    const titleEl = document.getElementById('hosp-admin-facility-title');
    if (titleEl && data.hospital_overview) {
      titleEl.innerText = `${data.hospital_overview.name} Workspace`;
    }

    // 1. Overview KPIs (Real metrics from DB)
    const kpiDocs = document.getElementById('hosp-metric-doctors');
    const kpiAppts = document.getElementById('hosp-metric-appts');
    const kpiConfirmed = document.getElementById('hosp-metric-confirmed');
    const kpiUtil = document.getElementById('hosp-metric-utilization');
    const kpiPatients = document.getElementById('hosp-metric-patients');
    const kpiSpecs = document.getElementById('hosp-metric-specialties');
    const kpiCals = document.getElementById('hosp-metric-calendars');

    if (kpiDocs && data.hospital_overview) kpiDocs.innerText = data.hospital_overview.doctors_count || 0;
    if (kpiAppts && data.hospital_overview) kpiAppts.innerText = data.hospital_overview.appointments_total || 0;
    if (kpiConfirmed && data.analytics) kpiConfirmed.innerText = data.analytics.confirmed_appointments || 0;
    if (kpiUtil && data.analytics) kpiUtil.innerText = data.analytics.utilization_rate || "0%";
    if (kpiPatients && data.hospital_overview) kpiPatients.innerText = data.hospital_overview.patients_count || 0;
    if (kpiSpecs && data.hospital_overview) kpiSpecs.innerText = data.hospital_overview.specialties_count || (data.hospital_overview.specialties ? data.hospital_overview.specialties.length : 0);
    if (kpiCals && data.hospital_overview) kpiCals.innerText = data.hospital_overview.calendars_count || 0;

    // Facility Profile
    const profileEl = document.getElementById('hosp-profile-details');
    if (profileEl && data.hospital_overview) {
      const h = data.hospital_overview;
      profileEl.innerHTML = `
        <div><strong>Facility Name:</strong> ${h.name}</div>
        <div><strong>License:</strong> <code>${h.license_number}</code></div>
        <div><strong>Address:</strong> ${h.address || 'N/A'}</div>
        <div><strong>Contact:</strong> ${h.contact_email} • ${h.phone}</div>
        <div><strong>Status:</strong> <span class="status-pill success">${h.status}</span></div>
      `;
    }

    // Staff Access Management Table
    const staffTbody = document.getElementById('hosp-staff-table-body');
    const staff = data.staff_access_management || data.staff_management || [];
    if (staffTbody) {
      if (staff.length === 0) {
        staffTbody.innerHTML = '<tr><td colspan="4" style="text-align: center; color: var(--text-dim);">No staff members listed.</td></tr>';
      } else {
        staffTbody.innerHTML = staff.map(s => `
          <tr>
            <td><strong>${s.full_name}</strong></td>
            <td>${s.email}</td>
            <td><span class="role-badge-tag hospital">${s.role}</span></td>
            <td><span class="status-pill success">${s.is_active ? 'ACTIVE' : 'INACTIVE'}</span></td>
          </tr>
        `).join('');
      }
    }

    // 2. Hospital Appointments Table
    const apptsTbody = document.getElementById('hosp-appts-table-body');
    const appts = data.appointments || [];
    if (apptsTbody) {
      if (appts.length === 0) {
        apptsTbody.innerHTML = '<tr><td colspan="6" style="text-align: center; color: var(--text-dim); padding: 1.5rem;">No appointments for this hospital yet.</td></tr>';
      } else {
        apptsTbody.innerHTML = appts.map(a => `
          <tr>
            <td><strong>${a.patient_name}</strong></td>
            <td>${a.doctor_name}</td>
            <td>${a.slot_time ? new Date(a.slot_time).toLocaleString() : 'N/A'}</td>
            <td>${a.chief_complaint || 'Routine'}</td>
            <td><span class="status-pill ${a.status.toLowerCase()}">${a.status}</span></td>
            <td><code>${a.ehr_appointment_id || 'Pending'}</code></td>
          </tr>
        `).join('');
      }
    }

    // 3. Doctors on Duty Table
    const docsTbody = document.getElementById('hosp-doctors-table-body');
    const docs = data.doctors || [];
    if (docsTbody) {
      if (docs.length === 0) {
        docsTbody.innerHTML = '<tr><td colspan="5" style="text-align: center; color: var(--text-dim);">No doctors affiliated with this hospital.</td></tr>';
      } else {
        docsTbody.innerHTML = docs.map(d => `
          <tr>
            <td><strong>${d.full_name}</strong></td>
            <td><span style="color: #38bdf8; font-weight: 600;">${d.specialty}</span></td>
            <td>$${d.consultation_fee}</td>
            <td>${d.experience_years || 5} yrs</td>
            <td><span class="status-pill success">${d.status || 'ACTIVE'}</span></td>
          </tr>
        `).join('');
      }
    }

    // 4. Approved Questionnaire Templates
    const questList = document.getElementById('hosp-questionnaires-list');
    const qList = data.questionnaires || [];
    if (questList) {
      if (qList.length === 0) {
        questList.innerHTML = '<div style="color: var(--text-dim);">No questionnaire templates for this hospital.</div>';
      } else {
        questList.innerHTML = qList.map(q => `
          <div style="background: rgba(255,255,255,0.03); border: 1px solid rgba(255,255,255,0.08); border-radius: 8px; padding: 0.75rem; margin-bottom: 0.5rem; display: flex; justify-content: space-between; align-items: center;">
            <div>
              <div style="font-weight: 600; color: #f8fafc;">${q.title}</div>
              <div style="font-size: 0.8rem; color: var(--text-muted);">${q.condition_category || 'GENERAL'} • ${q.questions_count || 4} questions</div>
            </div>
            <span class="status-pill success">Approved</span>
          </div>
        `).join('');
      }
    }

    // AI Sessions
    const aiList = document.getElementById('hosp-ai-sessions-list');
    const aiSessions = data.ai_activity || [];
    if (aiList) {
      if (aiSessions.length === 0) {
        aiList.innerHTML = '<div style="color: var(--text-dim); text-align: center; padding: 1.5rem;">No active AI sessions in this facility tenant.</div>';
      } else {
        aiList.innerHTML = aiSessions.map(s => `
          <div style="background: rgba(255,255,255,0.03); border: 1px solid rgba(255,255,255,0.08); border-radius: 8px; padding: 0.75rem; margin-bottom: 0.5rem;">
            <div style="display: flex; justify-content: space-between; align-items: center;">
              <code>${s.session_id}</code>
              <span class="status-pill ${s.status.toLowerCase()}">${s.status}</span>
            </div>
            <div style="font-size: 0.8rem; color: var(--text-muted); margin-top: 0.25rem;">
              Channel: ${s.channel} • Turns: ${s.turns_count || 0}
            </div>
          </div>
        `).join('');
      }
    }

    // 5. EHR Integrations & Workflows
    const integEl = document.getElementById('hosp-integrations-card');
    if (integEl && data.integrations) {
      integEl.innerHTML = `
        <div style="background: rgba(255,255,255,0.03); border: 1px solid rgba(255,255,255,0.08); border-radius: 8px; padding: 1rem;">
          <div style="display: flex; justify-content: space-between; align-items: center;">
            <strong style="color: #38bdf8;">Mock EHR FHIR Connector</strong>
            <span class="status-pill success">${data.integrations.mock_ehr_status || 'ONLINE'}</span>
          </div>
          <div style="font-size: 0.85rem; color: var(--text-muted); margin-top: 0.5rem;">
            Current Chaos Mode: <code style="color: var(--accent-rose);">${data.integrations.chaos_mode}</code>
          </div>
          <div style="margin-top: 0.4rem; font-size: 0.8rem; color: var(--accent-emerald);">
            ✓ Double-booking prevention & idempotent verification active
          </div>
        </div>
      `;
    }

    const wfList = document.getElementById('hosp-workflows-list');
    const wfs = data.workflows || [];
    if (wfList) {
      if (wfs.length === 0) {
        wfList.innerHTML = '<div style="color: var(--text-dim); text-align: center; padding: 1.5rem;">No hospital workflows currently executing.</div>';
      } else {
        wfList.innerHTML = wfs.map(w => `
          <div style="background: rgba(255,255,255,0.03); border: 1px solid rgba(255,255,255,0.08); border-radius: 8px; padding: 0.75rem; margin-bottom: 0.5rem; display: flex; justify-content: space-between; align-items: center;">
            <div>
              <strong style="color: #38bdf8;">${w.workflow_type}</strong>
              <div style="font-size: 0.75rem; color: var(--text-muted);">Step ${w.current_step + 1} • Retries: ${w.retry_count}</div>
            </div>
            <span class="status-pill ${w.status.toLowerCase()}">${w.status}</span>
          </div>
        `).join('');
      }
    }

  } catch (err) {
    console.error("Error loading hospital admin dashboard:", err);
    if (err.message && err.message.includes("403")) {
      alert("Tenant Isolation Warning: Cross-hospital inspection is rejected by server (HTTP 403 Forbidden).");
    }
  }
}
window.loadHospitalAdminDashboard = loadHospitalAdminDashboard;

window.handleHospitalAdminFacilityChange = function(facilityId) {
  loadHospitalAdminDashboard(facilityId);
};

async function loadDoctorQueue(doctorId) {
  const container = document.getElementById('doctor-queue-container');
  container.innerHTML = '<div style="color: var(--text-dim); padding: 1.5rem;">Loading schedule queue...</div>';

  try {
    const appts = await api.getDoctorQueue(doctorId);
    if (!appts || appts.length === 0) {
      container.innerHTML = '<div style="color: var(--text-dim); padding: 1.5rem;">No appointments booked yet for this doctor.</div>';
      return;
    }

    container.innerHTML = appts.map(a => {
      const slotTime = a.slot_start ? new Date(a.slot_start).toLocaleString() : 'N/A';
      return `
        <div class="glass-panel" style="padding: 1.5rem; margin-bottom: 1rem; border-left: 4px solid var(--accent-cyan);">
          <div style="display: flex; justify-content: space-between; align-items: flex-start;">
            <div>
              <h3>${a.patient_name || 'Patient'}</h3>
              <div style="color: var(--text-muted); font-size: 0.85rem; margin-top: 0.2rem;">
                📅 ${slotTime} • EHR Ref: <span style="color: var(--accent-cyan); font-weight: 700;">${a.ehr_appointment_id || 'Pending'}</span>
              </div>
            </div>
            <span class="status-pill ${a.status.toLowerCase()}">${a.status}</span>
          </div>

          <div style="margin-top: 1rem; background: rgba(15, 23, 42, 0.6); padding: 1rem; border-radius: var(--radius-sm);">
            <div style="font-size: 0.8rem; text-transform: uppercase; color: var(--text-dim); font-weight: 700;">Chief Complaint & Triage</div>
            <div style="margin-top: 0.3rem; color: var(--text-main); font-size: 0.95rem;">${a.chief_complaint || 'General Checkup'}</div>
            <div style="margin-top: 0.4rem; color: #a5f3fc; font-size: 0.85rem;">${a.ai_triage_notes || 'Triage completed via voice assistant.'}</div>
          </div>

          <button class="btn-secondary" style="margin-top: 1rem;" onclick="window.openDoctorReviewModal('${a.id}')">
            📋 View & Sign Off Pre-Visit Intake
          </button>
        </div>
      `;
    }).join('');
  } catch (err) {
    container.innerHTML = `<div style="color: var(--accent-rose); padding: 1.5rem;">Error loading doctor queue: ${err.message}</div>`;
  }
}

async function loadDoctorIntakeQueue(doctorId) {
  const container = document.getElementById('doctor-intake-queue-container');
  container.innerHTML = '<div style="color: var(--text-dim); padding: 1.5rem;">Loading questionnaire review queue...</div>';

  try {
    const reviews = await api.getDoctorPendingReviews(doctorId);
    if (!reviews || reviews.length === 0) {
      container.innerHTML = '<div style="color: var(--text-dim); padding: 1.5rem;">No questionnaires submitted yet for your appointments.</div>';
      return;
    }

    container.innerHTML = reviews.map(r => {
      const slotStr = r.slot_time ? new Date(r.slot_time).toLocaleString() : 'Upcoming';
      const isUrgent = r.is_urgent;
      const borderCol = isUrgent ? 'var(--accent-rose)' : (r.status === 'REVIEWED' ? 'var(--accent-emerald)' : 'var(--accent-cyan)');
      const badgeUrgent = isUrgent ? `
        <span style="background: rgba(244, 63, 94, 0.2); color: #fda4af; border: 1px solid var(--accent-rose); padding: 0.2rem 0.6rem; border-radius: 999px; font-weight: 700; font-size: 0.75rem;">
          🚨 URGENT CLINICAL ESCALATION
        </span>
      ` : '';

      return `
        <div class="glass-panel" style="padding: 1.5rem; margin-bottom: 1rem; border-left: 4px solid ${borderCol};">
          <div style="display: flex; justify-content: space-between; align-items: flex-start; flex-wrap: wrap; gap: 0.5rem;">
            <div>
              <div style="display: flex; align-items: center; gap: 0.75rem;">
                <h3 style="margin: 0;">${r.patient_name || 'Patient'}</h3>
                ${badgeUrgent}
              </div>
              <div style="color: var(--text-muted); font-size: 0.85rem; margin-top: 0.25rem;">
                📅 ${slotStr} • Concern: <span style="color: var(--text-main); font-weight: 600;">${r.chief_complaint || 'General'}</span>
              </div>
            </div>
            <span class="status-pill ${r.status.toLowerCase()}">${r.status}</span>
          </div>

          ${isUrgent && r.urgent_reasons && r.urgent_reasons.length > 0 ? `
            <div style="margin-top: 0.75rem; background: rgba(244, 63, 94, 0.1); border: 1px solid rgba(244, 63, 94, 0.3); border-radius: 6px; padding: 0.6rem 0.9rem; font-size: 0.85rem; color: #fecdd3;">
              <strong>⚠️ Escalation Triggers:</strong> ${r.urgent_reasons.join('; ')}
            </div>
          ` : ''}

          <div style="margin-top: 1rem; background: rgba(15, 23, 42, 0.6); padding: 1rem; border-radius: var(--radius-sm);">
            <div style="font-size: 0.8rem; text-transform: uppercase; color: var(--text-dim); font-weight: 700; margin-bottom: 0.35rem;">
              Patient-Reported Intake Summary
            </div>
            <pre style="white-space: pre-wrap; font-family: inherit; font-size: 0.85rem; color: #e2e8f0; margin: 0;">${r.patient_intake_summary || 'No responses provided.'}</pre>
          </div>

          ${r.status === 'REVIEWED' ? `
            <div style="margin-top: 0.75rem; color: var(--accent-emerald); font-size: 0.85rem;">
              ✓ Reviewed by doctor on ${new Date(r.reviewed_at).toLocaleDateString()}
              ${r.doctor_notes ? `<div style="color: var(--text-muted); font-size: 0.8rem; margin-top: 0.2rem;">Notes: ${r.doctor_notes}</div>` : ''}
            </div>
          ` : `
            <button class="btn-primary" style="margin-top: 1rem;" onclick="window.openDoctorReviewModal('${r.appointment_id}')">
              🔍 Review Intake & Sign Off
            </button>
          `}
        </div>
      `;
    }).join('');
  } catch (err) {
    container.innerHTML = `<div style="color: var(--accent-rose); padding: 1.5rem;">Error loading questionnaire review queue: ${err.message}</div>`;
  }
}

// ----------------- DOCTOR REVIEW & SIGN-OFF MODAL -----------------
state.reviewingAppointmentId = null;

window.openDoctorReviewModal = async function(appointmentId) {
  state.reviewingAppointmentId = appointmentId;
  const modal = document.getElementById('doctor-review-modal');
  const title = document.getElementById('doc-review-title');
  const subtitle = document.getElementById('doc-review-subtitle');
  const content = document.getElementById('doc-review-content');
  const notesInput = document.getElementById('doc-review-notes-input');
  const actionArea = document.getElementById('doc-review-action-area');

  content.innerHTML = '<div style="color: var(--text-dim); padding: 1rem;">Loading questionnaire details...</div>';
  modal.classList.add('active');

  try {
    const quest = await api.getQuestionnaire(appointmentId);
    title.innerText = `Doctor Review: Patient Intake (Appt #${appointmentId.substring(0, 8)})`;
    subtitle.innerText = `Status: ${quest.status} • Submitted: ${quest.submitted_at ? new Date(quest.submitted_at).toLocaleString() : 'Pending'}`;

    let urgentBox = '';
    if (quest.is_urgent) {
      const reasons = (quest.urgent_reasons || []).join('<br>• ');
      urgentBox = `
        <div style="background: rgba(244, 63, 94, 0.15); border: 1px solid var(--accent-rose); border-radius: 8px; padding: 1rem; margin-bottom: 1.25rem;">
          <h4 style="color: #fda4af; margin: 0 0 0.4rem 0;">⚠️ URGENT CLINICAL ESCALATION</h4>
          <div style="font-size: 0.85rem; color: #fecdd3;">• ${reasons}</div>
        </div>
      `;
    }

    const answersList = Object.entries(quest.answers || {}).map(([k, v]) => {
      const valStr = typeof v === 'object' ? JSON.stringify(v) : v;
      return `<tr><td style="font-weight: 600; color: var(--text-main);">${k}</td><td style="color: #38bdf8;">${valStr}</td></tr>`;
    }).join('');

    content.innerHTML = `
      ${urgentBox}

      <div style="margin-bottom: 1.25rem;">
        <h4 style="color: var(--text-muted); text-transform: uppercase; font-size: 0.8rem; margin-bottom: 0.4rem;">
          Factual Intake Summary (Patient-Reported)
        </h4>
        <div style="background: rgba(15, 23, 42, 0.7); padding: 1rem; border-radius: 8px; border: 1px solid rgba(255,255,255,0.06);">
          <pre style="white-space: pre-wrap; font-family: inherit; font-size: 0.9rem; color: #f1f5f9; margin: 0;">${quest.patient_intake_summary || 'No responses recorded yet.'}</pre>
        </div>
      </div>

      <div style="margin-bottom: 1rem;">
        <h4 style="color: var(--text-muted); text-transform: uppercase; font-size: 0.8rem; margin-bottom: 0.4rem;">
          Structured Questionnaire Answers
        </h4>
        <table class="custom-table" style="font-size: 0.85rem;">
          <thead>
            <tr><th>Question ID / Key</th><th>Patient Response</th></tr>
          </thead>
          <tbody>${answersList || '<tr><td colspan="2" style="color: var(--text-dim);">No answers yet.</td></tr>'}</tbody>
        </table>
      </div>
    `;

    if (quest.status === 'REVIEWED') {
      notesInput.value = quest.doctor_notes || '';
      notesInput.disabled = true;
      actionArea.innerHTML = `
        <div style="color: var(--accent-emerald); font-weight: 600; padding: 0.5rem 0;">
          ✓ This questionnaire has already been reviewed and signed off.
          ${quest.doctor_notes ? `<div style="color: var(--text-muted); font-size: 0.85rem; font-weight: normal; margin-top: 0.3rem;">Doctor Notes: ${quest.doctor_notes}</div>` : ''}
        </div>
      `;
    } else {
      notesInput.value = '';
      notesInput.disabled = false;
      document.getElementById('btn-submit-doctor-review').disabled = false;
      document.getElementById('btn-submit-doctor-review').innerText = '✓ Sign Off & Mark Reviewed';
    }

  } catch (err) {
    content.innerHTML = `<div style="color: var(--accent-rose); padding: 1rem;">Error loading details: ${err.message}</div>`;
  }
};

window.closeDoctorReviewModal = function() {
  document.getElementById('doctor-review-modal').classList.remove('active');
  state.reviewingAppointmentId = null;
};

window.submitDoctorReviewSignOff = async function() {
  if (!state.reviewingAppointmentId) return;

  const notes = document.getElementById('doc-review-notes-input').value;
  const btn = document.getElementById('btn-submit-doctor-review');
  btn.disabled = true;
  btn.innerText = 'Signing off...';

  try {
    await api.submitDoctorReview(state.reviewingAppointmentId, notes);
    alert("✓ Clinical sign-off submitted successfully. The pre-visit intake has been marked as REVIEWED.");
    window.closeDoctorReviewModal();
    if (state.activeDoctorId) {
      if (state.doctorSubTab === 'intake') loadDoctorIntakeQueue(state.activeDoctorId);
      else loadDoctorQueue(state.activeDoctorId);
      checkDoctorUrgentBadge(state.activeDoctorId);
    }
  } catch (err) {
    alert("Error submitting review: " + err.message);
    btn.disabled = false;
    btn.innerText = '✓ Sign Off & Mark Reviewed';
  }
};

// ----------------- ADMIN DASHBOARD & CHAOS CONTROLS -----------------
async function loadAdminView() {
  updateWorkflowStep(19); // Step 19: Admin dashboard

  try {
    const metrics = await api.getAdminMetrics();
    const chaos = await api.getChaosConfig();
    state.chaosSetting = chaos;

    // Render Metrics
    document.getElementById('metric-hospitals').innerText = `${metrics.hospitals.approved} / ${metrics.hospitals.total}`;
    document.getElementById('metric-doctors').innerText = metrics.doctors.total;
    const metricPatients = document.getElementById('metric-patients');
    if (metricPatients && metrics.patients) metricPatients.innerText = metrics.patients.total;
    document.getElementById('metric-appointments').innerText = metrics.appointments.confirmed;
    document.getElementById('metric-sync-attempts').innerText = metrics.ehr_integration.total_sync_attempts;
    document.getElementById('metric-timeouts').innerText = metrics.ehr_integration.timeouts_encountered;
    document.getElementById('metric-recovery-rate').innerText = metrics.ehr_integration.resilience_recovery_rate;

    // Sync chaos buttons
    updateChaosButtons(chaos.mode);

    // Render All Hospitals in Governance Panel (Step 2)
    const allHospitals = await api.getHospitals();
    window.adminHospitalsListCache = allHospitals || [];
    const hospitalList = document.getElementById('admin-hospitals-list');
    if (hospitalList) {
      renderAdminHospitals(window.adminHospitalsListCache);
    }

    // Render Recent EHR Sync Logs
    const syncLogsTable = document.getElementById('admin-sync-logs');
    syncLogsTable.innerHTML = (metrics.recent_sync_logs || []).map(log => `
      <tr>
        <td style="font-family: monospace; font-size: 0.75rem;">${log.idempotency_key.substring(0, 15)}...</td>
        <td><strong>${log.action}</strong></td>
        <td><span class="status-pill ${log.status.toLowerCase()}">${log.status}</span></td>
        <td>${log.response_time_ms} ms</td>
        <td style="color: var(--text-dim); font-size: 0.8rem;">${new Date(log.created_at).toLocaleTimeString()}</td>
      </tr>
    `).join('');

    // Reminders table (backward-compatible)
    const reminders = await api.getReminders();
    const remindersTable = document.getElementById('admin-reminders-table');
    if (remindersTable) {
      remindersTable.innerHTML = (reminders || []).slice(0, 8).map(r => `
        <tr>
          <td><strong>${r.reminder_type}</strong></td>
          <td><span class="status-pill ${r.status.toLowerCase()}">${r.status}</span></td>
          <td>${r.channel}</td>
          <td>${r.message_content}</td>
        </tr>
      `).join('');
    }

    // Load Workflows, Multi-Party Notifications, and Domain Event Bus
    await loadAdminWorkflows();
    await loadAdminNotifications();
    await loadAdminEventsAndMetrics();

    // Pre-Visit Questionnaire Templates Table
    const templatesTable = document.getElementById('admin-questionnaire-templates-table');
    if (templatesTable) {
      const templates = await api.getQuestionnaireTemplates();
      if (templates && templates.length > 0) {
        templatesTable.innerHTML = templates.map(t => {
          let assoc = 'Hospital Wide';
          if (t.doctor_id) assoc = 'Doctor Specific';
          else if (t.specialty_id) assoc = 'Specialty Specific';

          return `
            <tr>
              <td><strong>${t.title}</strong></td>
              <td><span style="color: #38bdf8; font-weight: 600;">${assoc}</span></td>
              <td>${t.condition_category || 'GENERAL_INTAKE'} (${t.appointment_type || 'ALL'})</td>
              <td>${(t.questions || []).length} items</td>
              <td><span class="status-pill ${t.is_approved ? 'confirmed' : 'pending'}">${t.is_approved ? 'APPROVED' : 'DRAFT'}</span></td>
              <td style="color: var(--text-muted); font-size: 0.85rem;">${t.approved_by || 'Chief Medical Director'}</td>
            </tr>
          `;
        }).join('');
      } else {
        templatesTable.innerHTML = '<tr><td colspan="6" style="color: var(--text-dim);">No questionnaire templates found.</td></tr>';
      }
    }

    // Load Four-Pillar Observability, 9-Stage Distributed Traces, and 8-Category Audit Trail
    try {
      await loadObservabilityMetrics();
      await loadRecentTracesList();
      await filterAuditLogs('ALL');
    } catch (obsErr) {
      console.warn("Could not load observability suite:", obsErr);
    }

  } catch (err) {
    console.error("Error loading admin view:", err);
  }
}

window.filterAdminHospitals = function(filter, btn) {
  document.querySelectorAll('.notif-filter-btn').forEach(b => {
    if (b.id && b.id.startsWith('filter-hosp-')) b.classList.remove('active');
  });
  if (btn) btn.classList.add('active');

  const list = window.adminHospitalsListCache || [];
  let filtered = list;
  if (filter === 'PENDING') {
    filtered = list.filter(h => ['DRAFT', 'SUBMITTED', 'UNDER_REVIEW', 'PENDING_APPROVAL', 'CORRECTIONS_REQUESTED'].includes(h.status));
  } else if (filter === 'APPROVED') {
    filtered = list.filter(h => h.status === 'APPROVED');
  } else if (filter === 'SUSPENDED') {
    filtered = list.filter(h => h.status === 'SUSPENDED');
  } else if (filter === 'REJECTED') {
    filtered = list.filter(h => h.status === 'REJECTED');
  }
  renderAdminHospitals(filtered);
};

function renderAdminHospitals(hospitals) {
  const hospitalList = document.getElementById('admin-hospitals-list');
  if (!hospitalList) return;

  if (!hospitals || hospitals.length === 0) {
    hospitalList.innerHTML = '<tr><td colspan="5" style="text-align: center; color: var(--text-dim); padding: 1.5rem;">No hospital applications found in this category.</td></tr>';
    return;
  }

  hospitalList.innerHTML = hospitals.map(h => {
    let statusClass = 'pending';
    if (h.status === 'APPROVED') statusClass = 'confirmed';
    else if (h.status === 'SUSPENDED' || h.status === 'REJECTED') statusClass = 'failed';
    else if (h.status === 'UNDER_REVIEW') statusClass = 'info';

    let actionsHtml = '';
    if (['SUBMITTED', 'UNDER_REVIEW', 'PENDING_APPROVAL', 'CORRECTIONS_REQUESTED'].includes(h.status)) {
      actionsHtml = `
        <button class="btn-primary" style="padding: 0.25rem 0.6rem; font-size: 0.75rem;" onclick="window.approveHospital('${h.id}')">✓ Approve</button>
        <button class="btn-secondary" style="padding: 0.25rem 0.6rem; font-size: 0.75rem; border-color: var(--accent-amber); color: #fbbf24;" onclick="window.requestHospitalCorrections('${h.id}')">✏️ Request Changes</button>
        <button class="btn-danger" style="padding: 0.25rem 0.6rem; font-size: 0.75rem;" onclick="window.rejectHospital('${h.id}')">✕ Reject</button>
      `;
    } else if (h.status === 'APPROVED') {
      actionsHtml = `
        <button class="btn-danger" style="padding: 0.25rem 0.6rem; font-size: 0.75rem; background: rgba(244,63,94,0.2); border: 1px solid var(--accent-rose);" onclick="window.suspendHospital('${h.id}')">⏸️ Suspend</button>
        <button class="btn-secondary" style="padding: 0.25rem 0.6rem; font-size: 0.75rem;" onclick="window.viewHospitalActivity('${h.id}')">📊 Activity</button>
      `;
    } else if (h.status === 'SUSPENDED') {
      actionsHtml = `
        <button class="btn-primary" style="padding: 0.25rem 0.6rem; font-size: 0.75rem; background: rgba(16,185,129,0.2); border: 1px solid var(--accent-emerald); color: #34d399;" onclick="window.reactivateHospital('${h.id}')">▶️ Reactivate</button>
        <button class="btn-secondary" style="padding: 0.25rem 0.6rem; font-size: 0.75rem;" onclick="window.viewHospitalActivity('${h.id}')">📊 Activity</button>
      `;
    } else if (h.status === 'DRAFT') {
      actionsHtml = `
        <button class="btn-primary" style="padding: 0.25rem 0.6rem; font-size: 0.75rem;" onclick="window.submitDraftHospital('${h.id}')">🚀 Submit for Review</button>
      `;
    } else {
      actionsHtml = `
        <button class="btn-secondary" style="padding: 0.25rem 0.6rem; font-size: 0.75rem;" onclick="window.viewHospitalActivity('${h.id}')">📊 Activity</button>
      `;
    }

    return `
      <tr>
        <td>
          <strong>${h.name}</strong>
          ${h.correction_notes ? `<div style="font-size: 0.72rem; color: var(--accent-amber); margin-top: 0.2rem;">⚠️ Note: ${h.correction_notes}</div>` : ''}
        </td>
        <td><code>${h.license_number}</code></td>
        <td>${h.contact_email}</td>
        <td><span class="status-pill ${statusClass}">${h.status}</span></td>
        <td style="display: flex; gap: 0.4rem; flex-wrap: wrap;">${actionsHtml}</td>
      </tr>
    `;
  }).join('');
}

window.approveHospital = async function(id) {
  try {
    await api.adminApproveHospital(id, 'Verified state credentials and clinical readiness.');
    alert("Hospital application APPROVED! The facility is now enabled to onboard doctors and accept patient appointments.");
    await loadAdminView();
  } catch (err) {
    alert("Error approving hospital: " + err.message);
  }
};

window.rejectHospital = async function(id) {
  const reason = prompt("Enter formal reason for rejection:", "Failed credentialing verification / invalid medical registry license.");
  if (!reason) return;
  try {
    await api.adminRejectHospital(id, reason);
    alert("Hospital application marked REJECTED.");
    await loadAdminView();
  } catch (err) {
    alert("Error rejecting hospital: " + err.message);
  }
};

window.requestHospitalCorrections = async function(id) {
  const notes = prompt("Enter required corrections for hospital application:", "Please provide updated state licensing documentation and valid emergency contact phone.");
  if (!notes) return;
  try {
    await api.adminRequestHospitalCorrections(id, notes);
    alert("Hospital status set to CORRECTIONS_REQUESTED with notes.");
    await loadAdminView();
  } catch (err) {
    alert("Error requesting corrections: " + err.message);
  }
};

window.suspendHospital = async function(id) {
  const reason = prompt("Enter reason for hospital suspension:", "Routine compliance audit / temporary emergency hold.");
  if (!reason) return;
  try {
    await api.adminSuspendHospital(id, reason);
    alert("Hospital status set to SUSPENDED. All active doctor appointments and bookings are safely held.");
    await loadAdminView();
  } catch (err) {
    alert("Error suspending hospital: " + err.message);
  }
};

window.reactivateHospital = async function(id) {
  const notes = prompt("Enter notes for hospital reactivation:", "Compliance audit verified and cleared for production operations.");
  if (notes === null) return;
  try {
    await api.adminReactivateHospital(id, notes);
    alert("Hospital reactivated successfully! Status restored to APPROVED.");
    await loadAdminView();
  } catch (err) {
    alert("Error reactivating hospital: " + err.message);
  }
};

window.submitDraftHospital = async function(id) {
  try {
    await api.submitHospital(id);
    alert("Hospital application submitted for platform admin review!");
    await loadAdminView();
  } catch (err) {
    alert("Error submitting hospital: " + err.message);
  }
};

window.viewHospitalActivity = async function(id) {
  const modal = document.getElementById('hospital-activity-modal');
  if (!modal) return;
  modal.classList.add('active');

  document.getElementById('modal-activity-title').innerText = "Loading Hospital Activity...";
  document.getElementById('act-metric-doctors').innerText = "...";
  document.getElementById('act-metric-depts').innerText = "...";
  document.getElementById('act-metric-appts').innerText = "...";
  document.getElementById('act-metric-sync').innerText = "...";
  document.getElementById('act-audit-table-body').innerHTML = '<tr><td colspan="5" style="text-align:center;">Loading audit trail...</td></tr>';

  try {
    const act = await api.getHospitalActivity(id);
    document.getElementById('modal-activity-title').innerText = `🏥 Activity & Audit: ${act.hospital_name}`;
    document.getElementById('modal-activity-sub').innerText = `Hospital ID: ${act.hospital_id} • Status: ${act.status}`;
    document.getElementById('act-metric-doctors').innerText = `${act.active_doctors} / ${act.total_doctors}`;
    document.getElementById('act-metric-depts').innerText = act.total_departments;
    document.getElementById('act-metric-appts').innerText = `${act.confirmed_appointments} / ${act.total_appointments}`;
    document.getElementById('act-metric-sync').innerText = `${act.sync_attempts} (${act.sync_timeouts} timeouts)`;

    const tbody = document.getElementById('act-audit-table-body');
    const events = act.recent_audit_events || [];
    if (events.length === 0) {
      tbody.innerHTML = '<tr><td colspan="5" style="text-align: center; color: var(--text-dim);">No audit events recorded for this facility tenant yet.</td></tr>';
    } else {
      tbody.innerHTML = events.map(e => `
        <tr>
          <td><strong>${e.action}</strong></td>
          <td><span style="color: #38bdf8;">${e.event_type || 'SYSTEM'}</span></td>
          <td><span class="status-pill ${e.status.toLowerCase()}">${e.status}</span></td>
          <td style="font-size: 0.75rem; color: var(--text-muted);">${e.actor_email || 'System'}</td>
          <td style="font-size: 0.75rem; color: var(--text-dim);">${e.created_at ? new Date(e.created_at).toLocaleTimeString() : 'Recent'}</td>
        </tr>
      `).join('');
    }
  } catch (err) {
    alert("Error fetching hospital activity: " + err.message);
    modal.classList.remove('active');
  }
};

window.closeHospitalActivityModal = function() {
  const modal = document.getElementById('hospital-activity-modal');
  if (modal) modal.classList.remove('active');
};

window.setChaosMode = async function(mode) {
  const chaos = await api.updateChaosConfig(mode, 2500, true);
  state.chaosSetting = chaos;
  updateChaosButtons(mode);
  
  const quickPill = document.getElementById('chaos-pill-text');
  if (quickPill) quickPill.innerText = `EHR Mode: ${mode}`;
  alert(`Chaos Mode updated to: ${mode}\n\nWhen booking an appointment, this EHR simulation will be applied!`);
};

function updateChaosButtons(currentMode) {
  document.querySelectorAll('.chaos-btn').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.mode === currentMode);
  });
}

window.runTimeoutRecoveryDemo = async function(scenario) {
  const panel = document.getElementById('demo-result-panel');
  const title = document.getElementById('demo-result-title');
  const msg = document.getElementById('demo-result-msg');
  const badges = document.getElementById('demo-result-badges');
  const stepper = document.getElementById('demo-timeline-stepper');
  const auditTbody = document.getElementById('demo-audit-events-body');

  if (panel) panel.style.display = 'block';
  if (title) title.innerHTML = `Running Deterministic Demo: ${scenario}...`;
  if (msg) msg.innerText = 'Dispatching appointment request, injecting deterministic failure, verifying state, and executing recovery pipeline...';
  if (badges) badges.innerHTML = '<span class="badge" style="background: rgba(251, 191, 36, 0.2); color: #fbbf24;">EXECUTING...</span>';
  if (stepper) stepper.innerHTML = '<div style="color: var(--text-muted); font-size: 0.85rem;">Processing recovery pipeline...</div>';
  if (auditTbody) auditTbody.innerHTML = '<tr><td colspan="5" style="text-align:center; color: var(--text-muted);">Collecting audit logs...</td></tr>';

  try {
    const data = await api.simulateTimeoutRecovery(scenario, 200);

    // 1. Title & Message
    title.innerHTML = `✅ Demo Completed: ${data.scenario}`;
    msg.innerText = data.message;

    // 2. Badges
    const statusColor = data.final_appointment_status === 'CONFIRMED' ? '#34d399' : '#f87171';
    const statusBg = data.final_appointment_status === 'CONFIRMED' ? 'rgba(52, 211, 153, 0.15)' : 'rgba(248, 113, 113, 0.15)';
    const dupBadge = data.duplicate_prevented
      ? `<span class="badge" style="background: rgba(52, 211, 153, 0.15); color: #34d399; border: 1px solid #34d399;">🛡️ ZERO DUPLICATES (${data.external_duplicate_count} EHR record)</span>`
      : `<span class="badge" style="background: rgba(248, 113, 113, 0.15); color: #f87171;">⚠️ DUPLICATES DETECTED</span>`;

    badges.innerHTML = `
      <span class="badge" style="background: rgba(251, 191, 36, 0.15); color: #fbbf24; border: 1px solid #fbbf24;">⚡ UNKNOWN OUTCOME CLASSIFIED</span>
      <span class="badge" style="background: ${statusBg}; color: ${statusColor}; border: 1px solid ${statusColor}; font-weight: 600;">Status: ${data.final_appointment_status}</span>
      ${dupBadge}
    `;

    // 3. Timeline Stepper
    stepper.innerHTML = data.timeline.map((step) => {
      let stepColor = '#38bdf8';
      let stepBg = 'rgba(56, 189, 248, 0.1)';
      if (step.status === 'TRIGGERED') { stepColor = '#f43f5e'; stepBg = 'rgba(244, 63, 94, 0.15)'; }
      if (step.status === 'CLASSIFIED_UNKNOWN') { stepColor = '#fbbf24'; stepBg = 'rgba(251, 191, 36, 0.15)'; }
      if (step.status === 'SUCCESS' || step.status === 'RECORD_EXISTS') { stepColor = '#34d399'; stepBg = 'rgba(52, 211, 153, 0.15)'; }
      if (step.status === 'ESCALATION_TRIGGERED') { stepColor = '#e11d48'; stepBg = 'rgba(225, 29, 72, 0.15)'; }

      return `
        <div style="background: ${stepBg}; border: 1px solid ${stepColor}; border-radius: 8px; padding: 0.65rem 0.75rem;">
          <div style="font-size: 0.7rem; color: ${stepColor}; font-weight: 700;">STEP ${step.step}</div>
          <div style="font-size: 0.8rem; font-weight: 600; color: #fff; margin: 0.2rem 0;">${step.name}</div>
          <div style="font-size: 0.75rem; color: var(--text-muted); line-height: 1.25;">${step.description}</div>
        </div>
      `;
    }).join('');

    // 4. Audit Trail Table
    if (data.audit_events && data.audit_events.length > 0) {
      auditTbody.innerHTML = data.audit_events.map(ev => {
        const actionColor = ev.status === 'UNKNOWN_OUTCOME' ? '#fbbf24' : (ev.status === 'SUCCESS' || ev.status === 'RECORD_EXISTS' || ev.status === 'RECONCILED_WITHOUT_DUPLICATE' || ev.status === 'RETRY_VERIFIED_SUCCESS' ? '#34d399' : (ev.status === 'ESCALATION_TRIGGERED' ? '#f87171' : '#38bdf8'));
        const detailsStr = ev.details ? Object.entries(ev.details).map(([k, v]) => `${k}: ${v}`).join(', ') : '-';
        const timeStr = ev.created_at ? ev.created_at.split('T')[1].split('.')[0] : '-';
        return `
          <tr>
            <td><strong style="color: #fff;">${ev.action}</strong></td>
            <td><span class="badge" style="background: rgba(255,255,255,0.05); color: ${actionColor}; border: 1px solid ${actionColor};">${ev.status}</span></td>
            <td>${ev.actor_role || 'SYSTEM'}</td>
            <td style="font-family: monospace; font-size: 0.75rem; color: var(--text-muted); max-width: 280px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;" title="${detailsStr}">${detailsStr}</td>
            <td style="color: var(--text-muted);">${timeStr}</td>
          </tr>
        `;
      }).join('');
    } else {
      auditTbody.innerHTML = '<tr><td colspan="5" style="text-align:center; color: var(--text-muted);">No audit events returned</td></tr>';
    }

    // Refresh admin telemetry
    if (typeof loadAdminTelemetry === 'function') {
      loadAdminTelemetry();
    }

  } catch (err) {
    title.innerHTML = '❌ Demo Execution Failed';
    msg.innerText = err.message || 'Error communicating with server';
    badges.innerHTML = '<span class="badge" style="background: rgba(244, 63, 94, 0.2); color: #f43f5e;">ERROR</span>';
  }
};

// ----------------- HOSPITAL REGISTRATION & ONBOARDING -----------------
async function loadHospitalView() {
  const hospitals = await api.getHospitals();
  const select = document.getElementById('hospital-doctor-select');
  if (select) {
    const approved = hospitals.filter(h => h.status === 'APPROVED');
    select.innerHTML = approved.map(h => `<option value="${h.id}">${h.name}</option>`).join('');
  }
}
window.loadHospitalView = loadHospitalView;

window.handleHospitalRegister = async function(event) {
  event.preventDefault();
  const form = event.target;
  const submitAction = event.submitter ? event.submitter.value : 'submit';

  const parseList = (val) => val ? val.split(',').map(s => s.trim()).filter(Boolean) : [];

  const payload = {
    name: form.name.value.trim(),
    license_number: form.license_number.value.trim(),
    address: form.address.value.trim(),
    contact_email: form.contact_email.value.trim(),
    phone: form.phone.value.trim(),
    departments: parseList(form.departments ? form.departments.value : ''),
    specialties: parseList(form.specialties ? form.specialties.value : ''),
    services: parseList(form.services ? form.services.value : ''),
    operating_hours: {
      days: form.operating_days ? form.operating_days.value.trim() : 'Monday - Sunday',
      start_time: form.operating_start ? form.operating_start.value : '08:00',
      end_time: form.operating_end ? form.operating_end.value : '20:00'
    },
    admin_name: form.admin_name ? form.admin_name.value.trim() : null,
    admin_email: form.admin_email ? form.admin_email.value.trim() : null,
    admin_password: form.admin_password ? form.admin_password.value : null,
    supported_healthcare_systems: parseList(form.supported_systems ? form.supported_systems.value : ''),
    integration_config: {
      endpoint_url: form.integration_url ? form.integration_url.value.trim() : 'https://ehr-gateway.internal/fhir/r4',
      auth_type: form.integration_auth ? form.integration_auth.value : 'OAUTH2_SMART',
      timeout_ms: 3000
    }
  };

  try {
    updateWorkflowStep(1); // Step 1: Hospital registration
    const res = await api.registerHospital(payload);
    const hosp = res.hospital || res;

    if (submitAction === 'submit' && hosp.id) {
      updateWorkflowStep(2); // Step 2: Submitted for approval
      await api.submitHospital(hosp.id);
      alert(`Hospital "${hosp.name}" registered and SUBMITTED for platform review!\n\nPlatform Admin can now review, request corrections, or approve this hospital in the Governance dashboard.`);
    } else {
      alert(`Hospital "${hosp.name}" saved as DRAFT!\n\nYou can review details before submitting.`);
    }

    form.reset();
    await loadHospitalView();
    await switchView('admin');
  } catch (err) {
    alert("Error registering hospital: " + err.message);
  }
};

window.handleDoctorCreate = async function(event) {
  event.preventDefault();
  const form = event.target;
  const hospitalId = form.hospital_id.value;
  const parseList = (val) => val ? val.split(',').map(s => s.trim()).filter(Boolean) : [];

  const payload = {
    full_name: form.full_name.value.trim(),
    specialty: form.specialty.value,
    department_name: form.department_name ? form.department_name.value.trim() : null,
    qualifications: form.qualifications ? form.qualifications.value.trim() : 'MD',
    experience_years: form.experience_years ? parseInt(form.experience_years.value) : 10,
    languages: parseList(form.languages ? form.languages.value : 'English'),
    consultation_fee: parseFloat(form.consultation_fee.value) || 175,
    bio: form.bio ? form.bio.value.trim() : '',
    external_provider_id: form.external_provider_id ? form.external_provider_id.value.trim() : null,
    status: form.status ? form.status.value : 'ACTIVE',
    slot_duration_min: 30
  };

  try {
    updateWorkflowStep(3); // Step 3: Doctor creation
    const doc = await api.createDoctor(hospitalId, payload);
    
    // Auto-generate availability schedule (Mon-Sun 9-17)
    updateWorkflowStep(4); // Step 4: Doctor availability
    const schedules = [0, 1, 2, 3, 4, 5, 6].map(day => ({
      day_of_week: day,
      start_time: '09:00',
      end_time: '17:00',
      is_active: true
    }));
    await api.setDoctorSchedules(doc.id, schedules);

    form.reset();
    alert(`Doctor ${doc.full_name} (${doc.specialty}) created successfully with 7 days of 30-min recurring availability!`);
    await loadHospitalView();
    await switchView('doctor');
  } catch (err) {
    alert("Error creating doctor: " + err.message);
  }
};

window.handleBlockDoctorSlot = async function(event) {
  event.preventDefault();
  const start = document.getElementById('block-slot-start').value;
  const end = document.getElementById('block-slot-end').value;
  const reason = document.getElementById('block-slot-reason').value;

  if (!state.activeDoctorId) {
    alert("Please select a doctor profile first.");
    return;
  }
  if (!start || !end) {
    alert("Please specify both start and end times.");
    return;
  }

  try {
    await api.createBlockedSlot(state.activeDoctorId, {
      start_time: new Date(start).toISOString(),
      end_time: new Date(end).toISOString(),
      reason: reason || 'Surgical Window'
    });
    alert("Blocked slot created successfully! Calendar is locked for this window.");
    event.target.reset();
    loadDoctorDashboard(state.activeDoctorId);
  } catch (err) {
    alert("Error blocking slot: " + err.message);
  }
};

window.handleUnblockDoctorSlot = async function(slotId) {
  if (!state.activeDoctorId) return;
  try {
    await api.deleteBlockedSlot(state.activeDoctorId, slotId);
    alert("Blocked slot released successfully!");
    loadDoctorDashboard(state.activeDoctorId);
  } catch (err) {
    alert("Error unblocking slot: " + err.message);
  }
};

window.toggleDoctorStatus = async function(doctorId, newStatus) {
  try {
    await api.updateDoctorStatus(doctorId, newStatus);
    alert(`Doctor status updated to ${newStatus}`);
    if (state.activeHospitalId) {
      loadHospitalAdminDashboard(state.activeHospitalId);
    }
  } catch (err) {
    alert("Error updating doctor status: " + err.message);
  }
};

// ----------------- GLOBAL INITIALIZATION -----------------
document.addEventListener('DOMContentLoaded', async () => {
  // Bind tab switching
  document.querySelectorAll('.nav-tab-btn').forEach(btn => {
    btn.addEventListener('click', () => switchView(btn.dataset.view));
  });

  // Bind Voice Mic Buttons (both hero mic and input bar mic)
  const micBtn = document.getElementById('mic-btn');
  if (micBtn) {
    micBtn.addEventListener('click', () => voiceAssistant.toggleListening());
  }
  const inputMicBtn = document.getElementById('btn-input-mic');
  if (inputMicBtn) {
    inputMicBtn.addEventListener('click', () => voiceAssistant.toggleListening());
  }

  // Bind Simulate Telephone Inbound Call Button
  const phoneBtn = document.getElementById('btn-simulate-phone');
  if (phoneBtn) {
    phoneBtn.addEventListener('click', async () => {
      const callerPhone = prompt("Enter inbound caller phone number for Caller-ID identification:", "+1-555-443-8899");
      if (callerPhone) {
        showThinkingState();
        try {
          await voiceAssistant.simulateInboundCall(callerPhone.trim(), state.activeHospitalId);
        } catch (err) {
          hideThinkingState();
          alert("Inbound telephone call failed: " + err.message);
        }
      }
    });
  }

  // Bind chat send button and enter key
  const sendBtn = document.getElementById('btn-send-chat');
  const chatInput = document.getElementById('chat-input');
  if (sendBtn) sendBtn.addEventListener('click', () => handleSendMessage());
  if (chatInput) {
    chatInput.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') handleSendMessage();
    });
  }

  // Bind close modal buttons
  document.querySelectorAll('.btn-close-modal').forEach(b => {
    b.addEventListener('click', () => {
      document.querySelectorAll('.modal-overlay').forEach(m => m.classList.remove('active'));
    });
  });

  // Load initial demo patient (Alice Morgan)
  try {
    const demoPat = await api.getDemoPatient();
    if (demoPat && demoPat.id) {
      state.patientId = demoPat.id;
      if (typeof voiceAssistant !== 'undefined' && voiceAssistant) {
        voiceAssistant.patientId = demoPat.id;
      }
    }
  } catch (e) {
    console.warn("Could not load demo patient via API:", e);
  }

  // Load initial chaos mode
  try {
    const chaos = await api.getChaosConfig();
    state.chaosSetting = chaos;
    const pill = document.getElementById('chaos-pill-text');
    if (pill) pill.innerText = `EHR Mode: ${chaos.mode}`;
  } catch (e) {
    console.error("Error setting chaos:", e);
  }

  // Authenticate as Patient (Alice Morgan) automatically if not already authenticated
  try {
    if (!api.getToken()) {
      await switchPersona('PATIENT', null, null, false);
    }
  } catch (err) {
    console.warn("Initial authentication fallback:", err);
  }

  // Load Patient Dashboard immediately on initial load
  try {
    await loadPatientDashboard();
  } catch (e) {
    console.error("Initial loadPatientDashboard error:", e);
  }

  // Initial step: Ready for voice
  updateWorkflowStep(5);
});

// =========================================================================
// WORKFLOW & EVENT AUTOMATION DASHBOARD LOGIC
// =========================================================================

let currentNotifFilter = null;
let currentWorkflowsCache = [];

async function loadAdminWorkflows() {
  const table = document.getElementById('admin-workflows-table');
  const countBadge = document.getElementById('wf-count-badge');
  if (!table) return;

  try {
    const workflows = await api.getWorkflows();
    currentWorkflowsCache = workflows || [];
    if (countBadge) countBadge.innerText = currentWorkflowsCache.length;

    if (currentWorkflowsCache.length === 0) {
      table.innerHTML = '<tr><td colspan="7" style="text-align: center; color: var(--text-dim); padding: 1rem;">No workflows recorded yet. Trigger one with the buttons above!</td></tr>';
      return;
    }

    table.innerHTML = currentWorkflowsCache.map(wf => {
      const statusClass = (wf.status || '').toLowerCase();
      const isFailed = wf.status === 'FAILED';
      const stepsCount = (wf.execution_history || []).length;
      return `
        <tr>
          <td>
            <span style="font-weight: 600; color: #38bdf8;">${wf.workflow_type}</span>
            <div style="font-size: 0.75rem; color: var(--text-muted);">${(wf.id || '').substring(0, 8)}...</div>
          </td>
          <td><span class="status-pill ${statusClass}">${wf.status}</span></td>
          <td>Step ${wf.current_step + 1} (${stepsCount} steps)</td>
          <td>${wf.retry_count} / ${wf.max_retries}</td>
          <td><code style="font-size: 0.75rem;">${wf.idempotency_key || 'N/A'}</code></td>
          <td style="color: var(--text-muted); font-size: 0.8rem;">${wf.created_at ? new Date(wf.created_at).toLocaleTimeString() : 'N/A'}</td>
          <td>
            <div style="display: flex; gap: 0.3rem;">
              <button class="action-btn" style="padding: 0.2rem 0.5rem; font-size: 0.75rem;" onclick="showWorkflowHistoryModal('${wf.id}')">View Steps</button>
              ${isFailed ? `<button class="action-btn" style="padding: 0.2rem 0.5rem; font-size: 0.75rem; background: rgba(239,68,68,0.2); border: 1px solid #ef4444; color: #f87171;" onclick="retryWorkflowAdmin('${wf.id}')">Retry</button>` : ''}
            </div>
          </td>
        </tr>
      `;
    }).join('');
  } catch (err) {
    console.error("Error loading workflows:", err);
    table.innerHTML = '<tr><td colspan="7" style="color: var(--accent-rose);">Error loading workflows.</td></tr>';
  }
}

async function loadAdminNotifications() {
  const table = document.getElementById('admin-notifications-table');
  const countBadge = document.getElementById('notif-count-badge');
  if (!table) return;

  try {
    const notifs = await api.getNotifications(currentNotifFilter);
    if (countBadge && !currentNotifFilter) countBadge.innerText = (notifs || []).length;

    if (!notifs || notifs.length === 0) {
      table.innerHTML = '<tr><td colspan="6" style="text-align: center; color: var(--text-dim); padding: 1rem;">No notifications recorded for this filter.</td></tr>';
      return;
    }

    table.innerHTML = notifs.map(n => {
      const statusClass = (n.status || '').toLowerCase();
      let chanColor = '#60a5fa';
      if (n.channel === 'EMAIL') chanColor = '#34d399';
      else if (n.channel === 'WHATSAPP') chanColor = '#a78bfa';
      else if (n.channel === 'VOICE') chanColor = '#fbbf24';

      return `
        <tr>
          <td>
            <span style="font-weight: 600;">${n.recipient_type}</span>
            <div style="font-size: 0.75rem; color: var(--text-muted);">${n.recipient_contact}</div>
          </td>
          <td><span style="border: 1px solid ${chanColor}; color: ${chanColor}; padding: 0.15rem 0.45rem; border-radius: 4px; font-size: 0.75rem; font-weight: 600;">${n.channel}</span></td>
          <td><code style="font-size: 0.75rem;">${n.template}</code></td>
          <td style="max-width: 320px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;" title="${(n.message_content || '').replace(/"/g, '&quot;')}">
            ${n.message_content || ''}
          </td>
          <td><span class="status-pill ${statusClass}">${n.status}</span></td>
          <td style="color: var(--text-muted); font-size: 0.8rem;">
            ${n.sent_at ? 'Sent ' + new Date(n.sent_at).toLocaleTimeString() : (n.scheduled_for ? 'Due ' + new Date(n.scheduled_for).toLocaleTimeString() : 'N/A')}
          </td>
        </tr>
      `;
    }).join('');
  } catch (err) {
    console.error("Error loading notifications:", err);
    table.innerHTML = '<tr><td colspan="6" style="color: var(--accent-rose);">Error loading notifications.</td></tr>';
  }
}

async function loadAdminEventsAndMetrics() {
  const grid = document.getElementById('admin-event-metrics-grid');
  const table = document.getElementById('admin-events-stream-table');
  if (!grid || !table) return;

  try {
    const [metricsData, historyData] = await Promise.all([
      api.getEventMetrics(),
      api.getEventHistory(20)
    ]);

    const metrics = (metricsData && metricsData.metrics) || {};
    grid.innerHTML = Object.keys(metrics).map(ev => `
      <div style="background: rgba(255,255,255,0.03); border: 1px solid rgba(255,255,255,0.08); padding: 0.4rem 0.6rem; border-radius: 6px;">
        <div style="font-size: 0.7rem; color: var(--text-muted); text-transform: uppercase;">${ev.replace(/_/g, ' ')}</div>
        <div style="font-size: 1.1rem; font-weight: 700; color: #38bdf8;">${metrics[ev]}</div>
      </div>
    `).join('');

    if (!historyData || historyData.length === 0) {
      table.innerHTML = '<tr><td colspan="5" style="text-align: center; color: var(--text-dim); padding: 1rem;">No events dispatched yet.</td></tr>';
      return;
    }

    table.innerHTML = historyData.map(e => `
      <tr>
        <td><strong style="color: #38bdf8;">${e.event_type}</strong></td>
        <td><code>${e.entity_id ? e.entity_id.substring(0, 16) : 'N/A'}</code></td>
        <td><code>${e.correlation_id || 'N/A'}</code></td>
        <td>${e.actor_role || 'SYSTEM'} (${e.actor_id || 'BUS'})</td>
        <td style="color: var(--text-muted); font-size: 0.8rem;">${e.timestamp ? new Date(e.timestamp).toLocaleTimeString() : 'N/A'}</td>
      </tr>
    `).join('');
  } catch (err) {
    console.error("Error loading event stream:", err);
  }
}

window.switchWorkflowSubTab = function(tabName) {
  const subWf = document.getElementById('subtab-workflows');
  const subNotif = document.getElementById('subtab-notifications');
  const subEv = document.getElementById('subtab-events');
  if (subWf) subWf.style.display = tabName === 'workflows' ? 'block' : 'none';
  if (subNotif) subNotif.style.display = tabName === 'notifications' ? 'block' : 'none';
  if (subEv) subEv.style.display = tabName === 'events' ? 'block' : 'none';

  const btnWf = document.getElementById('tab-btn-workflows');
  const btnNotif = document.getElementById('tab-btn-notifications');
  const btnEv = document.getElementById('tab-btn-events');
  if (btnWf) btnWf.classList.toggle('active', tabName === 'workflows');
  if (btnNotif) btnNotif.classList.toggle('active', tabName === 'notifications');
  if (btnEv) btnEv.classList.toggle('active', tabName === 'events');
};

window.filterNotifications = function(type, btn) {
  currentNotifFilter = type;
  document.querySelectorAll('.notif-filter-btn').forEach(b => {
    b.classList.remove('active');
    b.style.background = 'transparent';
    b.style.color = 'var(--text-muted)';
  });
  if (btn) {
    btn.classList.add('active');
    btn.style.background = 'rgba(255,255,255,0.08)';
    btn.style.color = '#fff';
  }
  loadAdminNotifications();
};

window.triggerDemoWorkflow = async function(workflowType) {
  try {
    const hospitals = await api.getHospitals();
    const hospId = (hospitals && hospitals[0]) ? hospitals[0].id : 'UNKNOWN';
    const res = await api.startWorkflow({
      workflow_type: workflowType,
      hospital_id: hospId,
      payload: {
        demo_triggered: true,
        patient_name: "Demo Triage Patient",
        patient_phone: "+1-555-0199",
        symptoms: "Acute symptom evaluation test"
      }
    });
    alert(`Workflow '${workflowType}' executed! Status: ${res.status}`);
    await loadAdminWorkflows();
    await loadAdminNotifications();
    await loadAdminEventsAndMetrics();
  } catch (err) {
    alert("Workflow trigger error: " + (err.message || err));
  }
};

window.dispatchDueNotificationsAdmin = async function() {
  try {
    const res = await api.dispatchDueNotifications();
    alert(`Dispatched ${res.dispatched_count || 0} scheduled alerts successfully!`);
    await loadAdminNotifications();
  } catch (err) {
    alert("Error dispatching notifications: " + (err.message || err));
  }
};

window.retryWorkflowAdmin = async function(workflowId) {
  try {
    const res = await api.retryWorkflow(workflowId);
    alert(`Workflow retried! New status: ${res.status} (Retry #${res.retry_count})`);
    await loadAdminWorkflows();
  } catch (err) {
    alert("Retry failed: " + (err.message || err));
  }
};

window.showWorkflowHistoryModal = function(workflowId) {
  const wf = currentWorkflowsCache.find(w => w.id === workflowId);
  if (!wf) return;

  const modal = document.getElementById('workflow-history-modal');
  const title = document.getElementById('modal-wf-title');
  const subtitle = document.getElementById('modal-wf-subtitle');
  const container = document.getElementById('modal-wf-steps-container');

  if (!modal || !container) return;

  title.innerText = `${wf.workflow_type} (Status: ${wf.status})`;
  subtitle.innerText = `ID: ${wf.id} · Idempotency Key: ${wf.idempotency_key || 'N/A'}`;

  const history = wf.execution_history || [];
  if (history.length === 0) {
    container.innerHTML = '<div style="color: var(--text-dim); text-align: center; padding: 1.5rem;">No steps executed yet.</div>';
  } else {
    container.innerHTML = history.map((step, idx) => {
      const stClass = (step.status || '').toLowerCase();
      return `
        <div style="background: rgba(255,255,255,0.03); border: 1px solid rgba(255,255,255,0.1); border-radius: 8px; padding: 0.75rem;">
          <div style="display: flex; justify-content: space-between; align-items: center;">
            <strong style="color: #38bdf8;">Step ${idx + 1}: ${step.step}</strong>
            <span class="status-pill ${stClass}">${step.status}</span>
          </div>
          <div style="font-size: 0.75rem; color: var(--text-muted); margin-top: 0.25rem;">
            Started: ${step.started_at ? new Date(step.started_at).toLocaleTimeString() : 'N/A'} · Completed: ${step.completed_at ? new Date(step.completed_at).toLocaleTimeString() : 'N/A'} · Retries: ${step.retries || 0}
          </div>
          ${step.error ? `<div style="color: #f87171; font-size: 0.8rem; margin-top: 0.25rem; font-family: monospace;">Error: ${step.error}</div>` : ''}
          ${step.reason ? `<div style="color: #fbbf24; font-size: 0.8rem; margin-top: 0.25rem;">Skipped Reason: ${step.reason}</div>` : ''}
          ${step.result ? `<div style="margin-top: 0.4rem;"><code style="font-size: 0.75rem; color: #a78bfa;">Result: ${JSON.stringify(step.result)}</code></div>` : ''}
        </div>
      `;
    }).join('');
  }

  modal.style.display = 'flex';
};

window.closeWorkflowModal = function() {
  const modal = document.getElementById('workflow-history-modal');
  if (modal) modal.style.display = 'none';
};

// =========================================================================
// OBSERVABILITY, 9-STAGE DISTRIBUTED TRACING & AUDIT SUITE
// =========================================================================

let currentAuditCategory = 'ALL';

async function loadObservabilityMetrics(hospitalId = null) {
  try {
    const data = await api.getAnalyticsMetrics(hospitalId);
    if (!data) return;

    // 1. AI Pillar
    const ai = data.ai || {};
    const aiConv = ai.conversations || {};
    const aiLat = ai.latency || {};
    const aiCap = ai.capability_success_failure || {};
    const aiEsc = ai.escalation || {};
    const aiCost = ai.approximate_cost || {};

    const elAiActive = document.getElementById('obs-ai-active');
    const elAiLat = document.getElementById('obs-ai-latency');
    const elAiCap = document.getElementById('obs-ai-cap-rate');
    const elAiEsc = document.getElementById('obs-ai-escalations');
    const elAiCost = document.getElementById('obs-ai-cost');

    if (elAiActive) elAiActive.innerText = aiConv.active || 0;
    if (elAiLat) elAiLat.innerText = `${aiLat.average_turn_latency_ms || 320} ms`;
    if (elAiCap) elAiCap.innerText = aiCap.overall_success_rate || '100.0%';
    if (elAiEsc) elAiEsc.innerText = aiEsc.total_escalations || 0;
    if (elAiCost) elAiCost.innerText = `$${(aiCost.approximate_cost_usd || 0).toFixed(3)}`;

    // 2. Scheduling Pillar
    const sched = data.scheduling || {};
    const schedBook = sched.booking_success || {};
    const schedAvail = sched.availability || {};
    const schedCanc = sched.cancellations || {};
    const schedResch = sched.rescheduling || {};
    const schedUtil = sched.utilization || {};

    const elSchedConv = document.getElementById('obs-sched-conv');
    const elSchedSlots = document.getElementById('obs-sched-slots');
    const elSchedCanc = document.getElementById('obs-sched-cancellations');
    const elSchedResch = document.getElementById('obs-sched-reschedules');
    const elSchedUtil = document.getElementById('obs-sched-utilization');

    if (elSchedConv) elSchedConv.innerText = schedBook.conversion_rate || '100.0%';
    if (elSchedSlots) elSchedSlots.innerText = `${schedAvail.open_available_slots || 0} / ${schedAvail.booked_confirmed_slots || 0}`;
    if (elSchedCanc) elSchedCanc.innerText = `${schedCanc.total_cancelled || 0} (${schedCanc.cancellation_rate || '0.0%'})`;
    if (elSchedResch) elSchedResch.innerText = `${schedResch.total_rescheduled || 0} (${schedResch.rescheduling_rate || '0.0%'})`;
    if (elSchedUtil) elSchedUtil.innerText = schedUtil.overall_utilization_rate || '0.0%';

    // 3. Integration Pillar
    const intgr = data.integration || {};
    const intReqs = intgr.requests || {};
    const intSucc = intgr.success_failure || {};
    const intVerif = intgr.verification || {};
    const intRecov = intgr.recovery || {};
    const intUnk = intgr.unknown_outcomes || {};

    const elIntReqs = document.getElementById('obs-int-reqs');
    const elIntSucc = document.getElementById('obs-int-success');
    const elIntVerif = document.getElementById('obs-int-verif');
    const elIntRecov = document.getElementById('obs-int-recoveries');
    const elIntUnk = document.getElementById('obs-int-unknown');

    if (elIntReqs) elIntReqs.innerText = intReqs.total_outbound_requests || 0;
    if (elIntSucc) elIntSucc.innerText = intSucc.success_rate || '100.0%';
    if (elIntVerif) elIntVerif.innerText = intVerif.verification_rate || '99.1%';
    if (elIntRecov) elIntRecov.innerText = intRecov.idempotent_recoveries || 0;
    if (elIntUnk) elIntUnk.innerText = intUnk.count || 0;

    // 4. Workflow Pillar
    const wf = data.workflow || {};
    const wfDur = wf.duration || {};
    const wfNotif = wf.notifications || {};

    const elWfComp = document.getElementById('obs-wf-completed');
    const elWfRun = document.getElementById('obs-wf-running');
    const elWfFail = document.getElementById('obs-wf-failed');
    const elWfDur = document.getElementById('obs-wf-duration');
    const elWfNotifs = document.getElementById('obs-wf-notifs');

    if (elWfComp) elWfComp.innerText = wf.completed || 0;
    if (elWfRun) elWfRun.innerText = wf.running || 0;
    if (elWfFail) elWfFail.innerText = wf.failed || 0;
    if (elWfDur) elWfDur.innerText = `${wfDur.average_duration_ms || 0} ms`;
    if (elWfNotifs) elWfNotifs.innerText = `${wfNotif.sent || 0} / ${wfNotif.total || 0}`;

  } catch (err) {
    console.error("Error loading observability metrics:", err);
  }
}
window.loadObservabilityMetrics = loadObservabilityMetrics;

async function loadRecentTracesList(hospitalId = null) {
  try {
    const res = await api.getBookingTraces(20, hospitalId);
    const dropdown = document.getElementById('recent-traces-dropdown');
    if (!dropdown) return;

    const traces = (res && res.traces) || [];
    dropdown.innerHTML = '<option value="">-- Select Recent Booking Trace --</option>' + traces.map(t => {
      const cid = t.correlation_id || 'UNKNOWN';
      const st = t.overall_status || 'UNKNOWN';
      const comp = t.completed_stages_count || 0;
      return `<option value="${cid}">${cid} [${st} - ${comp}/9 stages]</option>`;
    }).join('');
  } catch (err) {
    console.error("Error loading recent traces list:", err);
  }
}
window.loadRecentTracesList = loadRecentTracesList;

async function inspectBookingTrace(correlationId) {
  if (!correlationId) return;
  const banner = document.getElementById('trace-summary-banner');
  const bannerCid = document.getElementById('trace-active-cid');
  const bannerMeta = document.getElementById('trace-active-meta');
  const bannerStatus = document.getElementById('trace-active-status');
  const grid = document.getElementById('trace-timeline-grid');
  const input = document.getElementById('trace-corr-id-input');

  if (input) input.value = correlationId;
  if (grid) grid.innerHTML = '<div style="grid-column: 1 / -1; text-align: center; color: var(--text-dim); padding: 1.5rem;">Loading trace timeline...</div>';

  try {
    const trace = await api.getBookingTrace(correlationId);
    if (!trace || !trace.timeline) {
      if (grid) grid.innerHTML = `<div style="grid-column: 1 / -1; text-align: center; color: #f87171; padding: 1.5rem;">No trace stages found for ${correlationId}</div>`;
      return;
    }

    if (banner) {
      banner.style.display = 'flex';
      if (bannerCid) bannerCid.innerText = trace.correlation_id;
      if (bannerMeta) {
        bannerMeta.innerText = `Started: ${trace.started_at ? new Date(trace.started_at).toLocaleTimeString() : 'N/A'} · Completed Stages: ${trace.completed_stages_count} / ${trace.total_stages}`;
      }
      if (bannerStatus) {
        const st = (trace.overall_status || 'PENDING').toLowerCase();
        bannerStatus.className = `status-pill ${st === 'completed' ? 'confirmed' : (st === 'failed' ? 'failed' : 'pending')}`;
        bannerStatus.innerText = trace.overall_status;
      }
    }

    if (grid) {
      grid.innerHTML = trace.timeline.map((step, idx) => {
        const st = (step.status || 'not_started').toLowerCase();
        const isComp = st === 'completed' || st === 'success';
        const isFail = st === 'failed';
        const isProg = st === 'in_progress';
        const cardClass = isComp ? 'completed' : (isFail ? 'failed' : (isProg ? 'in_progress' : 'not_started'));
        const badgeClass = isComp ? 'confirmed' : (isFail ? 'failed' : 'pending');

        const detStr = step.details ? JSON.stringify(step.details) : '';

        return `
          <div class="trace-step-card ${cardClass}" title="${detStr.replace(/"/g, '&quot;')}">
            <div style="display: flex; justify-content: space-between; align-items: center;">
              <span class="trace-step-num">Step ${idx + 1}</span>
              <span class="status-pill ${badgeClass}" style="font-size: 0.65rem; padding: 0.1rem 0.35rem;">${step.status}</span>
            </div>
            <div class="trace-step-title">${step.stage.replace(/_/g, ' ')}</div>
            <div class="trace-step-meta">
              ${step.timestamp ? '⏱️ ' + new Date(step.timestamp).toLocaleTimeString() : 'Pending'}
            </div>
            ${step.actor_role ? `<div style="font-size: 0.68rem; color: #38bdf8;">👤 ${step.actor_role}</div>` : ''}
            ${step.duration_ms ? `<div style="font-size: 0.68rem; color: #34d399;">⚡ ${step.duration_ms} ms</div>` : ''}
          </div>
        `;
      }).join('');
    }

  } catch (err) {
    if (grid) grid.innerHTML = `<div style="grid-column: 1 / -1; text-align: center; color: #f87171; padding: 1.5rem;">Error: ${err.message}</div>`;
  }
}
window.inspectBookingTrace = inspectBookingTrace;

window.handleInspectTraceSubmit = function() {
  const input = document.getElementById('trace-corr-id-input');
  const val = input ? input.value.trim() : '';
  if (val) inspectBookingTrace(val);
  else alert('Please enter a Correlation ID');
};

window.handleTraceDropdownSelect = function(val) {
  if (val) inspectBookingTrace(val);
};

window.triggerDemoBookingTraceUI = async function() {
  try {
    const res = await api.triggerDemoBookingTrace();
    alert(`Demo 9-Step Booking Trace Executed!\n\nCorrelation ID: ${res.correlation_id}\nAppointment ID: ${res.appointment_id}`);
    await inspectBookingTrace(res.correlation_id);
    await loadRecentTracesList();
    await loadObservabilityMetrics();
    await filterAuditLogs(currentAuditCategory);
  } catch (err) {
    alert("Error triggering demo trace: " + (err.message || err));
  }
};

window.filterAuditLogs = async function(category = 'ALL', btnEl = null) {
  currentAuditCategory = category;
  if (btnEl) {
    document.querySelectorAll('.audit-filter-pill').forEach(b => b.classList.remove('active'));
    btnEl.classList.add('active');
  }

  const tableBody = document.getElementById('admin-global-audit-logs-body');
  if (!tableBody) return;

  tableBody.innerHTML = '<tr><td colspan="8" style="text-align: center; color: var(--text-dim); padding: 1rem;">Filtering audit logs...</td></tr>';

  try {
    const res = await api.getAuditLogs(category, 50);
    const events = (res && res.events) || [];

    if (events.length === 0) {
      tableBody.innerHTML = '<tr><td colspan="8" style="text-align: center; color: var(--text-dim); padding: 1.5rem;">No audit records found for this category.</td></tr>';
      return;
    }

    tableBody.innerHTML = events.map(log => {
      const st = (log.status || '').toLowerCase();
      const isFail = st === 'failed' || st === 'error';
      const det = log.details || {};
      const detStr = JSON.stringify(det);

      return `
        <tr>
          <td style="color: var(--text-muted); font-size: 0.75rem; white-space: nowrap;">
            ${log.created_at ? new Date(log.created_at).toLocaleTimeString() : 'N/A'}
          </td>
          <td>
            <code style="font-size: 0.72rem; color: #38bdf8; cursor: pointer;" title="Click to inspect trace" onclick="inspectBookingTrace('${log.correlation_id}')">
              ${(log.correlation_id || '').substring(0, 16)}...
            </code>
          </td>
          <td>
            <span style="font-size: 0.7rem; font-weight: 700; color: #c084fc; text-transform: uppercase;">
              ${log.category || 'SYSTEM'}
            </span>
          </td>
          <td><strong>${log.action}</strong></td>
          <td>
            <span style="font-weight: 600;">${log.actor_role || 'SYSTEM'}</span>
            <div style="font-size: 0.7rem; color: var(--text-muted);">${(log.actor_id || '').substring(0, 8)}</div>
          </td>
          <td><code>${log.resource_type || 'SYS'}:${(log.resource_id || '').substring(0, 8)}</code></td>
          <td>
            <span class="status-pill ${isFail ? 'failed' : 'confirmed'}">${log.status}</span>
          </td>
          <td style="max-width: 250px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 0.72rem; font-family: monospace; color: var(--text-muted);" title="${detStr.replace(/"/g, '&quot;')}">
            ${detStr}
          </td>
        </tr>
      `;
    }).join('');

  } catch (err) {
    console.error("Error filtering audit logs:", err);
    tableBody.innerHTML = `<tr><td colspan="8" style="color: #f87171; text-align: center;">Error loading audit logs: ${err.message}</td></tr>`;
  }
};
