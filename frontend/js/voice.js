import { api } from './api.js';

/**
 * Real-Time Voice Assistant Client
 * Architecture:
 * Voice Audio -> Speech Recognition -> AI Agent -> Controlled Capabilities -> Scheduling/EHR -> Speech Generation -> Patient
 *
 * Supports:
 * - Bidirectional WebSocket streaming with REST fallback
 * - Natural turn-taking with 1.2s silence detection
 * - Interruption / Barge-in with instantaneous speech cancellation
 * - Interim acoustic fillers for perceived latency masking (<200ms)
 * - Inbound telephone call simulation with Caller-ID identification
 */
export class VoiceAssistant {
  constructor({
    sessionId = null,
    patientId = null,
    hospitalId = null,
    onTranscript = null,
    onTurnSubmitted = null,
    onStateChange = null,
    onInterimFiller = null,
    onAssistantSpeech = null,
    onInterrupted = null,
    onEmergency = null
  } = {}) {
    this.sessionId = sessionId || `voice-${Date.now()}-${Math.random().toString(36).substring(2, 7)}`;
    this.patientId = patientId;
    this.hospitalId = hospitalId;
    this.channel = 'web_voice';

    // Callbacks
    this.onTranscript = onTranscript;
    this.onTurnSubmitted = onTurnSubmitted;
    this.onStateChange = onStateChange;
    this.onInterimFiller = onInterimFiller;
    this.onAssistantSpeech = onAssistantSpeech;
    this.onInterrupted = onInterrupted;
    this.onEmergency = onEmergency;

    // State
    this.isListening = false;
    this.isSpeaking = false;
    this.isThinking = false;
    this.callState = 'IDLE'; // IDLE, CONNECTED, LISTENING, USER_SPEAKING, THINKING, SPEAKING, INTERRUPTED
    this.connectionMode = 'DISCONNECTED'; // 'WS', 'REST', 'DISCONNECTED'

    // WebSocket & Audio
    this.ws = null;
    this.recognition = null;
    this.synthesis = window.speechSynthesis || null;
    this.currentUtterance = null;
    this.speechQueue = [];

    // Silence detection & buffer
    this.silenceTimeoutMs = 2800; // 2.8s for natural conversational turn-taking without premature cutoffs
    this.silenceTimer = null;
    this.accumulatedTranscript = '';
    this.lastInterimText = '';
    this.currentSpokenText = '';
    this.speechStartTime = 0;
    this.lastSpeechEndTime = 0;
    this.isRecognitionPausedForTTS = false;

    this.initRecognition();
    this.connectWebSocket();
  }

  // ---------------------------------------------------------------------
  // 1. WEBSOCKET STREAMING & LIFECYCLE
  // ---------------------------------------------------------------------

  connectWebSocket() {
    try {
      const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
      const host = window.location.host || (window.location.port ? `${window.location.hostname}:${window.location.port}` : window.location.hostname);
      const wsUrl = `${protocol}//${host}/ws/voice/${this.sessionId}`;

      this.ws = new WebSocket(wsUrl);

      this.ws.onopen = () => {
        this.connectionMode = 'WS';
        this.notifyState();
      };

      this.ws.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data);
          this.handleServerEvent(data);
        } catch (e) {
          console.error("Error parsing voice WebSocket message:", e);
        }
      };

      this.ws.onerror = (err) => {
        console.warn("Voice WebSocket encountered error, using REST fallback:", err);
        this.connectionMode = 'REST';
        this.notifyState();
      };

      this.ws.onclose = () => {
        if (this.connectionMode === 'WS') {
          this.connectionMode = 'REST';
          this.notifyState();
        }
      };
    } catch (e) {
      console.warn("WebSocket initialization failed, falling back to HTTP:", e);
      this.connectionMode = 'REST';
      this.notifyState();
    }
  }

  handleServerEvent(event) {
    const type = event.event_type;
    const payload = event.payload || {};

    switch (type) {
      case 'call_connected':
        this.callState = 'CONNECTED';
        this.notifyState("Connected to clinical voice assistant");
        break;

      case 'interrupted':
        this.callState = 'INTERRUPTED';
        this.cancelSpeech();
        if (this.onInterrupted) this.onInterrupted();
        this.notifyState("Interrupted - listening to you...");
        break;

      case 'assistant_thinking':
        this.isThinking = true;
        this.callState = 'THINKING';
        this.notifyState("Processing clinical intent...");
        break;

      case 'assistant_filler':
        this.isThinking = true;
        if (this.onInterimFiller) {
          this.onInterimFiller(payload);
        }
        // Speak interim acoustic filler to mask perceived latency
        if (payload.spoken_filler && !this.isListening) {
          this.speak(payload.spoken_filler, { isFiller: true });
        }
        break;

      case 'assistant_speech':
        this.isThinking = false;
        this.callState = 'SPEAKING';
        if (this.onAssistantSpeech) {
          this.onAssistantSpeech(payload);
        }
        if (payload.is_emergency && this.onEmergency) {
          this.onEmergency(payload);
        }
        // Speak final synthesized response
        if (payload.spoken_text) {
          this.speak(payload.spoken_text, { isFiller: false });
        }
        break;

      case 'call_ended':
        this.callState = 'DISCONNECTED';
        this.notifyState("Call ended");
        break;

      case 'call_error':
        this.isThinking = false;
        this.notifyState(`Error: ${payload.error || 'Unknown error'}`);
        break;
    }
  }

  // ---------------------------------------------------------------------
  // 2. SPEECH RECOGNITION (STT) & TURN-TAKING
  // ---------------------------------------------------------------------

  initRecognition() {
    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SpeechRecognition) {
      console.warn("Speech Recognition API is not supported in this browser.");
      return;
    }

    this.recognition = new SpeechRecognition();
    this.recognition.continuous = true;
    this.recognition.interimResults = true;
    this.recognition.lang = 'en-US';

    this.recognition.onstart = () => {
      // If recognition fired while assistant is speaking, abort immediately
      if (this.isSpeaking || this.isRecognitionPausedForTTS) {
        try { this.recognition.abort(); } catch (_) {}
        this.isListening = false;
        return;
      }
      this.isListening = true;
      this.callState = 'LISTENING';
      this.notifyState("Listening to your request...");
    };

    this.recognition.onresult = (event) => {
      // HARD DISCARD: If assistant is actively talking or mic was paused for TTS,
      // or within 1000ms after TTS stopped, never process or accumulate audio coming from the speakers!
      if (this.isSpeaking || this.isRecognitionPausedForTTS || (Date.now() - this.lastSpeechEndTime < 1000)) {
        return;
      }

      let interim = '';
      let final = '';

      for (let i = event.resultIndex; i < event.results.length; ++i) {
        const item = event.results[i];
        if (item.isFinal) {
          final += item[0].transcript;
        } else {
          interim += item[0].transcript;
        }
      }

      const heardText = (interim || final).trim();
      if (!heardText) return;

      if (interim) {
        this.lastInterimText = interim;
        this.callState = 'USER_SPEAKING';
        this.notifyState("Listening: " + interim);
        if (this.onTranscript) this.onTranscript(interim, false);

        // Notify server of interim transcript if WS active
        if (this.ws && this.ws.readyState === WebSocket.OPEN) {
          this.ws.send(JSON.stringify({
            event: 'interim_transcript',
            text: interim
          }));
        }

        // Reset silence timer on every user speech token
        this.resetSilenceTimer();
      }

      if (final) {
        this.accumulatedTranscript += (this.accumulatedTranscript ? ' ' : '') + final.trim();
        if (this.onTranscript) this.onTranscript(this.accumulatedTranscript, true);
        this.lastInterimText = '';
        this.resetSilenceTimer();
      }
    };

    this.recognition.onerror = (event) => {
      if (event.error !== 'no-speech' && event.error !== 'aborted') {
        console.warn("Speech recognition error:", event.error);
        this.isListening = false;
        this.notifyState(`Voice paused (${event.error})`);
      }
    };

    this.recognition.onend = () => {
      this.isListening = false;
      // CRITICAL: NEVER auto-restart microphone if assistant is speaking or paused for TTS!
      if (this.isSpeaking || this.isRecognitionPausedForTTS) {
        return;
      }
      // If we were supposed to be continuous listening and call is still active, restart
      if (this.callState !== 'DISCONNECTED' && this.shouldKeepListening) {
        try {
          this.recognition.start();
          this.isListening = true;
        } catch (e) {
          // ignore already started error
        }
      } else {
        this.notifyState("Microphone standby");
      }
    };
  }

  pauseRecognitionForSpeech() {
    this.isRecognitionPausedForTTS = true;
    if (this.silenceTimer) {
      clearTimeout(this.silenceTimer);
      this.silenceTimer = null;
    }
    this.accumulatedTranscript = '';
    this.lastInterimText = '';

    // Clear input field if holding any leaked speaker words
    const chatInput = document.getElementById('chat-input');
    if (chatInput && this.isSpeaking) {
      chatInput.value = '';
    }

    if (this.recognition) {
      try {
        this.recognition.abort();
      } catch (e) {}
    }
    this.isListening = false;
  }

  resumeRecognitionAfterSpeech() {
    this.isRecognitionPausedForTTS = false;
    this.accumulatedTranscript = '';
    this.lastInterimText = '';
    if (this.silenceTimer) {
      clearTimeout(this.silenceTimer);
      this.silenceTimer = null;
    }

    if (!this.recognition || this.isSpeaking) return;

    try {
      this.recognition.start();
      this.isListening = true;
      this.callState = 'LISTENING';
      this.notifyState("Listening to your request...");
    } catch (e) {
      this.isListening = true;
    }
  }

  resetSilenceTimer() {
    if (this.silenceTimer) {
      clearTimeout(this.silenceTimer);
    }

    this.silenceTimer = setTimeout(() => {
      this.handleSilenceTimeout();
    }, this.silenceTimeoutMs);
  }

  handleSilenceTimeout() {
    // If assistant is actively speaking or server is still thinking, do not cut off or double-submit
    if (this.isSpeaking || this.isThinking) return;

    const textToSend = (this.accumulatedTranscript || this.lastInterimText).trim();
    if (!textToSend || textToSend.length < 2) return;

    // Reset buffer
    this.accumulatedTranscript = '';
    this.lastInterimText = '';

    this.submitTurn(textToSend);
  }

  // ---------------------------------------------------------------------
  // 3. TURN SUBMISSION (STREAMING WS OR REST FALLBACK)
  // ---------------------------------------------------------------------

  async submitTurn(transcript) {
    if (!transcript) return;

    if (this.onTurnSubmitted) {
      this.onTurnSubmitted(transcript);
    }

    this.isThinking = true;
    this.callState = 'THINKING';
    this.notifyState("Thinking & checking records...");

    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      // Send turn via real-time WebSocket
      this.ws.send(JSON.stringify({
        event: 'final_transcript',
        text: transcript,
        patient_id: this.patientId,
        hospital_id: this.hospitalId
      }));
    } else {
      // Fallback to HTTP REST turn
      try {
        const res = await api.voiceTurn(
          this.sessionId,
          transcript,
          this.patientId,
          this.hospitalId,
          this.channel
        );

        this.isThinking = false;
        this.callState = 'SPEAKING';

        if (res.interim_filler && this.onInterimFiller) {
          this.onInterimFiller({ filler_text: res.interim_filler, spoken_filler: res.interim_filler });
        }

        if (this.onAssistantSpeech) {
          this.onAssistantSpeech(res);
        }

        if (res.is_emergency && this.onEmergency) {
          this.onEmergency(res);
        }

        if (res.spoken_text) {
          this.speak(res.spoken_text);
        }
      } catch (err) {
        this.isThinking = false;
        console.error("REST Voice turn error:", err);
        this.notifyState(`Error: ${err.message}`);
      }
    }
  }

  // ---------------------------------------------------------------------
  // 4. INTERRUPTION / BARGE-IN HANDLING
  // ---------------------------------------------------------------------

  triggerBargeIn() {
    // 1. Immediately cancel local speech output
    this.cancelSpeech();

    // 2. Notify backend to abort or cancel pending output
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify({
        event: 'speech_start'
      }));
    }

    this.callState = 'INTERRUPTED';
    if (this.onInterrupted) this.onInterrupted();
    this.notifyState("Interrupted - listening...");

    // 3. Immediately resume microphone for user response
    this.resumeRecognitionAfterSpeech();
  }

  cancelSpeech() {
    if (this.synthesis) {
      try {
        this.synthesis.cancel();
      } catch (e) {
        console.warn("Speech synthesis cancel error:", e);
      }
    }
    this.isSpeaking = false;
    this.currentUtterance = null;
    this.currentSpokenText = '';
    this.speechQueue = [];
    this.isRecognitionPausedForTTS = false;
  }

  // ---------------------------------------------------------------------
  // 5. SPEECH GENERATION (TTS)
  // ---------------------------------------------------------------------

  speak(text, { isFiller = false } = {}) {
    if (!this.synthesis || !text) return;

    // Do not play if user is actively speaking (barge-in guard)
    if (this.callState === 'USER_SPEAKING') return;

    try {
      // Cancel previous speech if not appending filler
      if (!isFiller) {
        this.synthesis.cancel();
      }

      // Strips markdown artifacts, emojis, and symbols for natural audio
      const clean = text
        .replace(/[*_#`~]/g, '')
        .replace(/[🩺⚡🏥👨‍⚕️⚠️•|]/g, '')
        .replace(/\$\s?(\d+)/g, '$1 dollars')
        .replace(/\bDr\.\s/gi, 'Doctor ')
        .replace(/\bEHR\b/g, 'E.H.R.')
        .replace(/\s+/g, ' ')
        .trim();

      if (!clean) return;

      const utterance = new SpeechSynthesisUtterance(clean);
      utterance.rate = 1.05; // natural conversational pacing
      utterance.pitch = 1.0;
      utterance.lang = 'en-US';

      utterance.onstart = () => {
        this.isSpeaking = true;
        this.currentUtterance = utterance;
        this.currentSpokenText = clean.toLowerCase();
        this.speechStartTime = Date.now();

        // HARD MUTE: Abort microphone while speech plays so speakers don't feed into microphone
        this.pauseRecognitionForSpeech();

        this.notifyState(isFiller ? "Assistant responding..." : "Assistant speaking...");
      };

      utterance.onend = () => {
        if (this.currentUtterance === utterance) {
          this.isSpeaking = false;
          this.currentUtterance = null;
          this.currentSpokenText = '';
          this.lastSpeechEndTime = Date.now();

          // Wait 800ms acoustic decay after speaker stops before unmuting mic
          setTimeout(() => {
            if (!this.isSpeaking && this.shouldKeepListening) {
              this.resumeRecognitionAfterSpeech();
            } else if (!this.isSpeaking) {
              this.notifyState("Microphone ready — listening for your response...");
            }
          }, 800);
        }
      };

      utterance.onerror = (e) => {
        // Interrupted errors are expected during barge-in
        if (e.error !== 'interrupted' && e.error !== 'canceled') {
          console.warn("Speech synthesis utterance error:", e);
        }
        this.isSpeaking = false;
        this.currentUtterance = null;
        this.currentSpokenText = '';
        this.lastSpeechEndTime = Date.now();
        setTimeout(() => {
          if (!this.isSpeaking && this.shouldKeepListening) {
            this.resumeRecognitionAfterSpeech();
          }
        }, 300);
      };

      this.synthesis.speak(utterance);
    } catch (e) {
      console.error("Speech synthesis error:", e);
    }
  }

  // ---------------------------------------------------------------------
  // 6. CONTROL METHODS
  // ---------------------------------------------------------------------

  startListening() {
    if (!this.recognition) {
      alert("Speech recognition is not supported in this browser. Please use the text input below.");
      return;
    }

    this.shouldKeepListening = true;
    try {
      this.recognition.start();
    } catch (err) {
      // Already running is okay
    }
  }

  stopListening() {
    this.shouldKeepListening = false;
    if (this.silenceTimer) {
      clearTimeout(this.silenceTimer);
      this.silenceTimer = null;
    }
    if (this.recognition) {
      try {
        this.recognition.stop();
      } catch (err) {
        // ignore
      }
    }
    this.isListening = false;
    this.notifyState("Voice recognition paused");
  }

  toggleListening() {
    if (this.isListening) {
      this.stopListening();
    } else {
      // If assistant is currently speaking, barge-in immediately
      if (this.isSpeaking) {
        this.triggerBargeIn();
      }
      this.startListening();
    }
  }

  // ---------------------------------------------------------------------
  // 7. INBOUND TELEPHONE CALL SIMULATION (PRD Section 11)
  // ---------------------------------------------------------------------

  async simulateInboundCall(callerPhone, hospitalId = null) {
    this.cancelSpeech();
    this.channel = 'telephone';
    this.notifyState(`Incoming call from ${callerPhone}...`);

    try {
      const callData = await api.inboundCall(callerPhone, hospitalId);
      this.sessionId = callData.session_id;
      this.patientId = callData.patient_id;
      this.hospitalId = callData.hospital_id;

      // Reconnect WebSocket with the new telephone session ID
      if (this.ws) {
        try { this.ws.close(); } catch (e) {}
      }
      this.connectWebSocket();

      this.callState = 'CONNECTED';

      if (this.onAssistantSpeech) {
        this.onAssistantSpeech({
          session_id: callData.session_id,
          spoken_text: callData.spoken_welcome,
          reply: callData.welcome_prompt,
          intent: 'TELEPHONE_INBOUND_WELCOME',
          capabilities_executed: ['lookup_patient'],
          needs_clarification: false,
          is_escalated: false,
          latency_ms: 60,
          call_state: 'CONNECTED'
        });
      }

      // Speak personalized telephone welcome
      this.speak(callData.spoken_welcome);
      return callData;
    } catch (err) {
      console.error("Error in inbound call simulation:", err);
      this.notifyState(`Telephone error: ${err.message}`);
      throw err;
    }
  }

  notifyState(statusMsg = null) {
    if (this.onStateChange) {
      this.onStateChange({
        isListening: this.isListening,
        isSpeaking: this.isSpeaking,
        isThinking: this.isThinking,
        callState: this.callState,
        connectionMode: this.connectionMode,
        statusText: statusMsg || (this.isListening ? "Listening..." : "Click microphone to speak")
      });
    }
  }
}
