const API_BASE = '/api';

let currentToken = localStorage.getItem('aegis_token') || null;

function authHeaders() {
  const headers = { 'Content-Type': 'application/json' };
  if (currentToken) {
    headers['Authorization'] = `Bearer ${currentToken}`;
  }
  return headers;
}

export const api = {
  setToken(token) {
    currentToken = token;
    if (token) localStorage.setItem('aegis_token', token);
    else localStorage.removeItem('aegis_token');
  },
  getToken() {
    return currentToken;
  },
  async login(email, password) {
    const res = await fetch(`${API_BASE}/auth/login`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email, password })
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || 'Login failed');
    }
    const data = await res.json();
    if (data.access_token) {
      this.setToken(data.access_token);
    }
    return data;
  },
  async healthCheck() {
    const res = await fetch(`${API_BASE}/health`);
    return res.json();
  },

  // --- Hospitals ---
  async getHospitals(status = null) {
    const url = status ? `${API_BASE}/hospitals?status=${status}` : `${API_BASE}/hospitals`;
    const res = await fetch(url);
    return res.json();
  },

  async registerHospital(data) {
    const res = await fetch(`${API_BASE}/hospitals/register`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data)
    });
    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail || 'Failed to register hospital');
    }
    return res.json();
  },

  async updateHospitalApproval(hospitalId, status, rejectionReason = null) {
    const res = await fetch(`${API_BASE}/hospitals/${hospitalId}/approval`, {
      method: 'PATCH',
      headers: authHeaders(),
      body: JSON.stringify({ status, rejection_reason: rejectionReason })
    });
    return res.json();
  },

  async submitHospital(hospitalId) {
    const res = await fetch(`${API_BASE}/hospitals/${hospitalId}/submit`, {
      method: 'POST',
      headers: authHeaders()
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || 'Failed to submit hospital application');
    }
    return res.json();
  },

  async getHospitalDetails(hospitalId) {
    const res = await fetch(`${API_BASE}/hospitals/${hospitalId}`, {
      headers: authHeaders()
    });
    return res.json();
  },

  async updateHospitalProfile(hospitalId, data) {
    const res = await fetch(`${API_BASE}/hospitals/${hospitalId}`, {
      method: 'PATCH',
      headers: authHeaders(),
      body: JSON.stringify(data)
    });
    return res.json();
  },

  async getHospitalApplications(status = null) {
    const url = status ? `${API_BASE}/admin/hospitals/applications?status=${status}` : `${API_BASE}/admin/hospitals/applications`;
    const res = await fetch(url, { headers: authHeaders() });
    return res.json();
  },

  async adminApproveHospital(hospitalId, notes = '') {
    const res = await fetch(`${API_BASE}/admin/hospitals/${hospitalId}/approve`, {
      method: 'POST',
      headers: authHeaders(),
      body: JSON.stringify({ notes })
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || 'Failed to approve hospital');
    }
    return res.json();
  },

  async adminRejectHospital(hospitalId, reason) {
    const res = await fetch(`${API_BASE}/admin/hospitals/${hospitalId}/reject`, {
      method: 'POST',
      headers: authHeaders(),
      body: JSON.stringify({ reason })
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || 'Failed to reject hospital');
    }
    return res.json();
  },

  async adminRequestHospitalCorrections(hospitalId, notes) {
    const res = await fetch(`${API_BASE}/admin/hospitals/${hospitalId}/request-corrections`, {
      method: 'POST',
      headers: authHeaders(),
      body: JSON.stringify({ notes })
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || 'Failed to request hospital corrections');
    }
    return res.json();
  },

  async adminSuspendHospital(hospitalId, reason) {
    const res = await fetch(`${API_BASE}/admin/hospitals/${hospitalId}/suspend`, {
      method: 'POST',
      headers: authHeaders(),
      body: JSON.stringify({ reason })
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || 'Failed to suspend hospital');
    }
    return res.json();
  },

  async adminReactivateHospital(hospitalId, notes = '') {
    const res = await fetch(`${API_BASE}/admin/hospitals/${hospitalId}/reactivate`, {
      method: 'POST',
      headers: authHeaders(),
      body: JSON.stringify({ notes })
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || 'Failed to reactivate hospital');
    }
    return res.json();
  },

  async getHospitalActivity(hospitalId) {
    const res = await fetch(`${API_BASE}/hospitals/${hospitalId}/activity`, {
      headers: authHeaders()
    });
    return res.json();
  },

  async listDepartments(hospitalId) {
    const res = await fetch(`${API_BASE}/hospitals/${hospitalId}/departments`, {
      headers: authHeaders()
    });
    return res.json();
  },

  async createDepartment(hospitalId, data) {
    const res = await fetch(`${API_BASE}/hospitals/${hospitalId}/departments`, {
      method: 'POST',
      headers: authHeaders(),
      body: JSON.stringify(data)
    });
    return res.json();
  },

  async updateDepartment(hospitalId, deptId, data) {
    const res = await fetch(`${API_BASE}/hospitals/${hospitalId}/departments/${deptId}`, {
      method: 'PATCH',
      headers: authHeaders(),
      body: JSON.stringify(data)
    });
    return res.json();
  },

  async listCalendars(hospitalId) {
    const res = await fetch(`${API_BASE}/hospitals/${hospitalId}/calendars`, {
      headers: authHeaders()
    });
    return res.json();
  },

  async createCalendar(hospitalId, data) {
    const res = await fetch(`${API_BASE}/hospitals/${hospitalId}/calendars`, {
      method: 'POST',
      headers: authHeaders(),
      body: JSON.stringify(data)
    });
    return res.json();
  },

  async getHospitalCommunicationPreferences(hospitalId) {
    const res = await fetch(`${API_BASE}/hospitals/${hospitalId}/communication-preferences`, {
      headers: authHeaders()
    });
    return res.json();
  },

  async updateHospitalCommunicationPreferences(hospitalId, prefs) {
    const res = await fetch(`${API_BASE}/hospitals/${hospitalId}/communication-preferences`, {
      method: 'PUT',
      headers: authHeaders(),
      body: JSON.stringify(prefs)
    });
    return res.json();
  },

  async updateDoctorStatus(doctorId, status, reason = null) {
    const res = await fetch(`${API_BASE}/doctors/${doctorId}/status`, {
      method: 'PATCH',
      headers: authHeaders(),
      body: JSON.stringify({ status, reason })
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || 'Failed to update doctor status');
    }
    return res.json();
  },

  async listBlockedSlots(doctorId) {
    const res = await fetch(`${API_BASE}/doctors/${doctorId}/blocked-slots`, {
      headers: authHeaders()
    });
    return res.json();
  },

  async createBlockedSlot(doctorId, data) {
    const res = await fetch(`${API_BASE}/doctors/${doctorId}/blocked-slots`, {
      method: 'POST',
      headers: authHeaders(),
      body: JSON.stringify(data)
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || 'Failed to block doctor slot');
    }
    return res.json();
  },

  async deleteBlockedSlot(doctorId, slotId) {
    const res = await fetch(`${API_BASE}/doctors/${doctorId}/blocked-slots/${slotId}`, {
      method: 'DELETE',
      headers: authHeaders()
    });
    return res.json();
  },

  // --- Doctors & Slots ---
  async getDoctors(specialty = null, hospitalId = null) {
    let url = `${API_BASE}/doctors?active_only=true`;
    if (specialty) url += `&specialty=${encodeURIComponent(specialty)}`;
    if (hospitalId) url += `&hospital_id=${encodeURIComponent(hospitalId)}`;
    const res = await fetch(url);
    return res.json();
  },

  async createDoctor(hospitalId, data) {
    const res = await fetch(`${API_BASE}/doctors/hospital/${hospitalId}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data)
    });
    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail || 'Failed to create doctor');
    }
    return res.json();
  },

  async setDoctorSchedules(doctorId, schedules) {
    const res = await fetch(`${API_BASE}/doctors/${doctorId}/schedules`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ schedules })
    });
    return res.json();
  },

  async getDoctorSlots(doctorId, date = null) {
    const url = date ? `${API_BASE}/doctors/${doctorId}/slots?date=${date}` : `${API_BASE}/doctors/${doctorId}/slots`;
    const res = await fetch(url);
    return res.json();
  },

  // --- AI Clinical Triage & Agent ---
  async agentChat(sessionId, message, patientId = null, hospitalId = null, channel = 'web_voice') {
    const res = await fetch(`${API_BASE}/ai/agent/chat`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        session_id: sessionId,
        message,
        patient_id: patientId,
        hospital_id: hospitalId,
        channel
      })
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || 'AI Agent interaction failed');
    }
    return res.json();
  },

  async getAgentContext(sessionId) {
    const res = await fetch(`${API_BASE}/ai/agent/context/${sessionId}`);
    return res.json();
  },

  async resetAgentSession(sessionId) {
    const res = await fetch(`${API_BASE}/ai/agent/reset/${sessionId}`, { method: 'POST' });
    return res.json();
  },

  async escalateToHuman(sessionId, reason, urgency = 'ROUTINE') {
    const res = await fetch(`${API_BASE}/ai/agent/escalate`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: sessionId, reason, urgency })
    });
    return res.json();
  },

  // --- Real-Time Voice & Telephone Interface ---
  async voiceTurn(sessionId, transcript, patientId = null, hospitalId = null, channel = 'web_voice') {
    const res = await fetch(`${API_BASE}/voice/turn`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        session_id: sessionId,
        transcript,
        patient_id: patientId,
        hospital_id: hospitalId,
        channel
      })
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || 'Voice turn processing failed');
    }
    return res.json();
  },

  async inboundCall(callerPhone, hospitalId = null) {
    const res = await fetch(`${API_BASE}/voice/inbound-call`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        caller_phone: callerPhone,
        hospital_id: hospitalId
      })
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || 'Inbound telephone call failed');
    }
    return res.json();
  },

  async triageChat(message, patientId = null) {
    const res = await fetch(`${API_BASE}/ai/triage/chat`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message, patient_id: patientId })
    });
    return res.json();
  },

  async getDemoPatient() {
    const res = await fetch(`${API_BASE}/patients/demo`);
    if (!res.ok) throw new Error('Could not fetch demo patient');
    return res.json();
  },

  // --- Patients Management ---
  async registerPatient(data) {
    const res = await fetch(`${API_BASE}/patients/register`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data)
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      let msg = 'Registration failed';
      if (typeof err.detail === 'string') {
        msg = err.detail;
      } else if (Array.isArray(err.detail)) {
        msg = err.detail.map(d => (d.loc && d.loc.length ? `${d.loc[d.loc.length - 1]}: ` : '') + (d.msg || JSON.stringify(d))).join('; ');
      }
      throw new Error(msg);
    }
    return res.json();
  },

  async getCurrentPatient() {
    const res = await fetch(`${API_BASE}/patients/me`, {
      headers: authHeaders()
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || 'Failed to fetch current patient');
    }
    return res.json();
  },

  async getPatientProfile(patientId) {
    const res = await fetch(`${API_BASE}/patients/${patientId}`, {
      headers: authHeaders()
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || 'Failed to fetch patient profile');
    }
    return res.json();
  },

  async updatePatientProfile(patientId, data) {
    const res = await fetch(`${API_BASE}/patients/${patientId}`, {
      method: 'PATCH',
      headers: authHeaders(),
      body: JSON.stringify(data)
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || 'Failed to update patient profile');
    }
    return res.json();
  },

  // --- Appointments ---
  async confirmAppointment(slotId, patientId, chiefComplaint, urgencyLevel, aiTriageNotes, idempotencyKey = null) {
    const res = await fetch(`${API_BASE}/appointments/confirm`, {
      method: 'POST',
      headers: authHeaders(),
      body: JSON.stringify({
        slot_id: slotId,
        patient_id: patientId || undefined,
        chief_complaint: chiefComplaint,
        urgency_level: urgencyLevel,
        ai_triage_notes: aiTriageNotes,
        idempotency_key: idempotencyKey
      })
    });
    if (!res.ok) {
      let errMsg = 'Failed to book appointment';
      try {
        const err = await res.json();
        if (typeof err.detail === 'string') {
          errMsg = err.detail;
        } else if (Array.isArray(err.detail)) {
          errMsg = err.detail.map(d => `${d.loc ? d.loc.slice(1).join('.') + ': ' : ''}${d.msg || JSON.stringify(d)}`).join(', ');
        } else if (err.detail && typeof err.detail === 'object') {
          errMsg = JSON.stringify(err.detail);
        } else if (err.message) {
          errMsg = err.message;
        }
      } catch (_) {
        errMsg = res.statusText || errMsg;
      }
      throw new Error(errMsg);
    }
    return res.json();
  },

  async rescheduleAppointment(appointmentId, newSlotId, reason = null) {
    const res = await fetch(`${API_BASE}/appointments/${appointmentId}/reschedule`, {
      method: 'POST',
      headers: authHeaders(),
      body: JSON.stringify({ new_slot_id: newSlotId, reason })
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || 'Failed to reschedule appointment');
    }
    return res.json();
  },

  async cancelAppointment(appointmentId, reason = null) {
    const res = await fetch(`${API_BASE}/appointments/${appointmentId}/cancel`, {
      method: 'POST',
      headers: authHeaders(),
      body: JSON.stringify({ reason })
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || 'Failed to cancel appointment');
    }
    return res.json();
  },

  async getAppointment(appointmentId) {
    const res = await fetch(`${API_BASE}/appointments/${appointmentId}`, {
      headers: authHeaders()
    });
    return res.json();
  },

  async getDoctorQueue(doctorId) {
    const res = await fetch(`${API_BASE}/appointments/doctor/${doctorId}/queue`);
    return res.json();
  },

  // --- Questionnaire ---
  async getQuestionnaire(appointmentId) {
    const res = await fetch(`${API_BASE}/questionnaires/${appointmentId}`);
    return res.json();
  },

  async submitQuestionnaire(appointmentId, answers) {
    const res = await fetch(`${API_BASE}/questionnaires/${appointmentId}/submit`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ answers })
    });
    return res.json();
  },

  async postConversationalTurn(appointmentId, patientUtterance) {
    const res = await fetch(`${API_BASE}/questionnaires/${appointmentId}/conversational-turn`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ appointment_id: appointmentId, patient_utterance: patientUtterance })
    });
    return res.json();
  },

  async getQuestionnaireTemplates(params = {}) {
    const query = new URLSearchParams(params).toString();
    const res = await fetch(`${API_BASE}/questionnaires/templates${query ? '?' + query : ''}`);
    return res.json();
  },

  async createQuestionnaireTemplate(data) {
    const res = await fetch(`${API_BASE}/questionnaires/templates`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data)
    });
    return res.json();
  },

  async getDoctorPendingReviews(doctorId) {
    const res = await fetch(`${API_BASE}/questionnaires/doctor/${doctorId}/pending-reviews`);
    return res.json();
  },

  async submitDoctorReview(appointmentId, doctorNotes) {
    const res = await fetch(`${API_BASE}/questionnaires/${appointmentId}/review`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ doctor_notes: doctorNotes })
    });
    return res.json();
  },

  // --- Mock EHR & Chaos ---
  async getChaosConfig() {
    const res = await fetch(`${API_BASE}/mock-ehr/chaos-config`);
    return res.json();
  },

  async updateChaosConfig(mode, simulatedDelayMs = 2500, failureActive = true) {
    const res = await fetch(`${API_BASE}/mock-ehr/chaos-config`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        mode,
        simulated_delay_ms: simulatedDelayMs,
        failure_active: failureActive
      })
    });
    return res.json();
  },

  async getEhrRecords() {
    const res = await fetch(`${API_BASE}/mock-ehr/records`);
    return res.json();
  },

  async simulateTimeoutRecovery(scenario, simulatedDelayMs = 200) {
    const res = await fetch(`${API_BASE}/mock-ehr/demo/simulate-timeout-recovery`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        scenario,
        simulated_delay_ms: simulatedDelayMs
      })
    });
    return res.json();
  },

  // --- Admin Metrics ---
  async getAdminMetrics() {
    const res = await fetch(`${API_BASE}/admin/metrics`);
    return res.json();
  },

  // --- Reminders ---
  async getReminders() {
    const res = await fetch(`${API_BASE}/reminders`);
    return res.json();
  },

  async dispatchPendingReminders() {
    const res = await fetch(`${API_BASE}/reminders/dispatch-pending`, { method: 'POST' });
    return res.json();
  },

  // --- Workflows & Event Automation ---
  async getWorkflows(hospitalId = null, status = null, workflowType = null) {
    let url = `${API_BASE}/workflows?limit=50`;
    if (hospitalId) url += `&hospital_id=${encodeURIComponent(hospitalId)}`;
    if (status) url += `&status=${encodeURIComponent(status)}`;
    if (workflowType) url += `&workflow_type=${encodeURIComponent(workflowType)}`;
    const res = await fetch(url);
    return res.json();
  },

  async getWorkflow(workflowId) {
    const res = await fetch(`${API_BASE}/workflows/${workflowId}`);
    return res.json();
  },

  async startWorkflow(data) {
    const res = await fetch(`${API_BASE}/workflows/start`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data)
    });
    return res.json();
  },

  async retryWorkflow(workflowId) {
    const res = await fetch(`${API_BASE}/workflows/${workflowId}/retry`, { method: 'POST' });
    return res.json();
  },

  // --- Multi-Party Notifications ---
  async getNotifications(recipientType = null, hospitalId = null, status = null) {
    let url = `${API_BASE}/notifications?limit=50`;
    if (recipientType) url += `&recipient_type=${encodeURIComponent(recipientType)}`;
    if (hospitalId) url += `&hospital_id=${encodeURIComponent(hospitalId)}`;
    if (status) url += `&status=${encodeURIComponent(status)}`;
    const res = await fetch(url);
    return res.json();
  },

  async sendNotification(data) {
    const res = await fetch(`${API_BASE}/notifications/send`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data)
    });
    return res.json();
  },

  async dispatchDueNotifications() {
    const res = await fetch(`${API_BASE}/notifications/dispatch`, { method: 'POST' });
    return res.json();
  },

  // --- Domain Events & Telemetry ---
  async publishEvent(data) {
    const res = await fetch(`${API_BASE}/events/publish`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data)
    });
    return res.json();
  },

  async getEventHistory(limit = 20, eventType = null) {
    let url = `${API_BASE}/events/history?limit=${limit}`;
    if (eventType) url += `&event_type=${encodeURIComponent(eventType)}`;
    const res = await fetch(url);
    return res.json();
  },

  async getEventMetrics() {
    const res = await fetch(`${API_BASE}/events/metrics`);
    return res.json();
  },

  // --- Role-Specific Dashboards ---
  async getPersonas() {
    const res = await fetch(`${API_BASE}/dashboard/personas`);
    return res.json();
  },

  async getPlatformAdminDashboard() {
    const res = await fetch(`${API_BASE}/dashboard/platform-admin`, {
      headers: authHeaders()
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || 'Failed to load platform admin dashboard');
    }
    return res.json();
  },

  async getHospitalAdminDashboard(hospitalId = null) {
    const url = hospitalId ? `${API_BASE}/dashboard/hospital-admin?hospital_id=${encodeURIComponent(hospitalId)}` : `${API_BASE}/dashboard/hospital-admin`;
    const res = await fetch(url, {
      headers: authHeaders()
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || 'Failed to load hospital admin dashboard');
    }
    return res.json();
  },

  async getDoctorDashboard(doctorId = null) {
    const url = doctorId ? `${API_BASE}/dashboard/doctor?doctor_id=${encodeURIComponent(doctorId)}` : `${API_BASE}/dashboard/doctor`;
    const res = await fetch(url, {
      headers: authHeaders()
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || 'Failed to load doctor dashboard');
    }
    return res.json();
  },

  async getPatientDashboard(patientId = null) {
    const url = patientId ? `${API_BASE}/dashboard/patient?patient_id=${encodeURIComponent(patientId)}` : `${API_BASE}/dashboard/patient`;
    const res = await fetch(url, {
      headers: authHeaders()
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || 'Failed to load patient dashboard');
    }
    return res.json();
  },

  async updatePatientPreferences(data) {
    const res = await fetch(`${API_BASE}/dashboard/patient/preferences`, {
      method: 'PUT',
      headers: authHeaders(),
      body: JSON.stringify(data)
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || 'Failed to update preferences');
    }
    return res.json();
  },

  async createDoctorBlockedSlot(data) {
    const res = await fetch(`${API_BASE}/dashboard/doctor/block-slot`, {
      method: 'POST',
      headers: authHeaders(),
      body: JSON.stringify(data)
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || 'Failed to block doctor slot');
    }
    return res.json();
  },

  async deleteDoctorBlockedSlot(slotId) {
    const res = await fetch(`${API_BASE}/dashboard/doctor/unblock-slot/${slotId}`, {
      method: 'DELETE',
      headers: authHeaders()
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || 'Failed to unblock slot');
    }
    return res.json();
  },

  // --- Analytics, Observability & Distributed Traces ---
  async getAnalyticsMetrics(hospitalId = null) {
    const url = hospitalId ? `${API_BASE}/analytics/metrics?hospital_id=${encodeURIComponent(hospitalId)}` : `${API_BASE}/analytics/metrics`;
    const res = await fetch(url, { headers: authHeaders() });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || 'Failed to load analytics metrics');
    }
    return res.json();
  },

  async getBookingTrace(correlationId) {
    const res = await fetch(`${API_BASE}/analytics/trace/${encodeURIComponent(correlationId)}`, { headers: authHeaders() });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || 'Failed to load trace');
    }
    return res.json();
  },

  async getBookingTraces(limit = 25, hospitalId = null) {
    let url = `${API_BASE}/analytics/traces?limit=${limit}`;
    if (hospitalId) url += `&hospital_id=${encodeURIComponent(hospitalId)}`;
    const res = await fetch(url, { headers: authHeaders() });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || 'Failed to load traces');
    }
    return res.json();
  },

  async getAuditLogs(category = null, limit = 50, hospitalId = null) {
    let url = `${API_BASE}/analytics/audit-logs?limit=${limit}`;
    if (category && category !== 'ALL') url += `&category=${encodeURIComponent(category)}`;
    if (hospitalId) url += `&hospital_id=${encodeURIComponent(hospitalId)}`;
    const res = await fetch(url, { headers: authHeaders() });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || 'Failed to load audit logs');
    }
    return res.json();
  },

  async triggerDemoBookingTrace() {
    const res = await fetch(`${API_BASE}/analytics/trace/demo-booking`, {
      method: 'POST',
      headers: authHeaders()
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || 'Failed to trigger demo booking trace');
    }
    return res.json();
  }
};
