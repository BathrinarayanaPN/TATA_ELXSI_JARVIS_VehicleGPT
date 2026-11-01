/**
 * Tata Elxsi SDV Luxury Automotive Cockpit & Copilot Client
 * Version 4.0 — All Sensors, Closed-Loop Reaction, CV Lane Perception, Ollama Qwen 1.5B
 */

// Application State
const state = {
  telemetry: {},
  autopilot: true,
  gear: 'D4',
  camView: 'cockpit',
  weather: 'ClearNoon',
  town: 'Town04',
  role: 'driver',
  lang: 'en',
  viewMode: 'simple',
  ttsEnabled: true,
  speechActive: false,
  activeFaults: new Set(),
  alarmActive: false,
  alarmMuted: false,
  lastProactiveFault: null
};

document.addEventListener('DOMContentLoaded', () => {
  initClock();
  initTelemetry();
  initAlarmSystem();
  initFaultInjectionControls();
  initCarlaControls();
  initCopilotAssistant();
  initSpeechFeatures();
});

/* ==========================================================================
   1. Live Clock & Top Bar
   ========================================================================== */

function initClock() {
  const clockEl = document.getElementById('disp-clock');
  function update() {
    const now = new Date();
    if (clockEl) {
      clockEl.textContent = now.toLocaleTimeString('en-US', { hour12: false });
    }
  }
  update();
  setInterval(update, 1000);
}

/* ==========================================================================
   1B. Acoustic Automotive Safety Alarm & Emergency Banner
   ========================================================================== */

let audioCtx = null;
let alarmInterval = null;

function playChimeTone() {
  if (state.alarmMuted) return;
  try {
    if (!audioCtx) {
      audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    }
    if (audioCtx.state === 'suspended') {
      audioCtx.resume();
    }
    const t0 = audioCtx.currentTime;
    const osc = audioCtx.createOscillator();
    const gain = audioCtx.createGain();
    osc.type = 'sawtooth';
    // Authentic automotive dual chime (880 Hz -> 587 Hz)
    osc.frequency.setValueAtTime(880, t0);
    osc.frequency.exponentialRampToValueAtTime(587, t0 + 0.22);

    gain.gain.setValueAtTime(0.3, t0);
    gain.gain.exponentialRampToValueAtTime(0.01, t0 + 0.35);

    osc.connect(gain);
    gain.connect(audioCtx.destination);
    osc.start(t0);
    osc.stop(t0 + 0.35);
  } catch (e) {
    console.warn('Audio alarm chime error:', e);
  }
}

function triggerAlarm(title, desc) {
  state.alarmActive = true;
  const banner = document.getElementById('emergency-alarm-banner');
  const titleEl = document.getElementById('alarm-banner-title');
  const descEl = document.getElementById('alarm-banner-desc');
  if (banner) banner.classList.remove('hidden');
  if (titleEl) titleEl.textContent = title || 'CRITICAL SAFETY ALARM ACTIVE';
  if (descEl) descEl.textContent = desc || 'Vehicle operating outside safety envelope. Pull over safely.';

  playChimeTone();
  if (!alarmInterval) {
    alarmInterval = setInterval(() => {
      if (state.alarmActive && !state.alarmMuted) {
        playChimeTone();
      }
    }, 2200);
  }
}

function stopAlarm() {
  state.alarmActive = false;
  if (alarmInterval) {
    clearInterval(alarmInterval);
    alarmInterval = null;
  }
  const banner = document.getElementById('emergency-alarm-banner');
  if (banner) banner.classList.add('hidden');
}

function initAlarmSystem() {
  const silenceBtn = document.getElementById('btn-silence-alarm');
  const silenceIcon = document.getElementById('silence-icon');
  const silenceLabel = document.getElementById('silence-label');
  const clearBtn = document.getElementById('btn-alarm-clear-all');

  if (silenceBtn) {
    silenceBtn.addEventListener('click', () => {
      state.alarmMuted = !state.alarmMuted;
      if (silenceIcon) silenceIcon.textContent = state.alarmMuted ? '🔇' : '🔊';
      if (silenceLabel) silenceLabel.textContent = state.alarmMuted ? 'Unmute Chime' : 'Silence Chime';
    });
  }

  if (clearBtn) {
    clearBtn.addEventListener('click', async () => {
      await executeFaultInjection('clear');
      stopAlarm();
    });
  }
}

/* ==========================================================================
   2. Real-Time Telemetry & Gauge Engine (10 Hz WebSocket + Live Poll)
   ========================================================================== */

function initTelemetry() {
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  const wsUrl = `${protocol}//${window.location.host}/ws/telemetry`;
  let ws = null;

  function connectWs() {
    try {
      ws = new WebSocket(wsUrl);
      ws.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data);
          renderTelemetry(data);
        } catch (e) {}
      };
      ws.onclose = () => setTimeout(connectWs, 2000);
      ws.onerror = () => ws.close();
    } catch (e) {
      setTimeout(connectWs, 3000);
    }
  }

  connectWs();

  // Polling fallback
  setInterval(async () => {
    if (!ws || ws.readyState !== WebSocket.OPEN) {
      try {
        const res = await fetch('/api/live');
        if (res.ok) {
          const data = await res.json();
          renderTelemetry(data);
        }
      } catch (e) {}
    }
  }, 800);
}

function renderTelemetry(data) {
  state.telemetry = data;

  const speed = Number(data.speed_kmh || 0);
  const soc = Number(data.soc_pct || 76);
  const temp = Number(data.batt_temp_c || 32.4);
  const range = Number(data.est_range_km || 275);
  const psiFl = Number(data.tire_psi_fl || 36.0);
  const pumpOk = Boolean(data.coolant_pump_ok ?? 1);
  const derate = Boolean(data.power_derate ?? 0);
  const power = Number(data.power_kw || 0.0);
  const volt = Number(data.batt_voltage || (350 + (soc * 0.45)));
  const curr = Number(data.batt_current_a || (power > 0 ? (power * 1000 / volt) : 0.0));
  const road = data.road_type || 'highway';
  const trip = data.trip_id || 80;

  // 1. Column 1: Primary Metrics
  setText('val-soc', `${Math.round(soc)}%`);
  setText('lbl-range', `${Math.round(range)} km Est. Range`);
  setText('val-temp', `${temp.toFixed(1)}°C`);
  setText('val-psi', `${psiFl.toFixed(0)} psi`);

  const battCondEl = document.getElementById('val-batt-cond');
  const battIconEl = document.getElementById('icon-batt-cond');
  if (battCondEl) {
    if (derate || !pumpOk) {
      battCondEl.textContent = 'Degraded';
      battCondEl.style.color = '#EF4444';
      if (battIconEl) battIconEl.className = 'tile-icon-circle yellow';
    } else if (temp > 50) {
      battCondEl.textContent = 'Warning';
      battCondEl.style.color = '#F59E0B';
      if (battIconEl) battIconEl.className = 'tile-icon-circle yellow';
    } else {
      battCondEl.textContent = 'Good';
      battCondEl.style.color = '#10B981';
      if (battIconEl) battIconEl.className = 'tile-icon-circle green';
    }
  }

  // Deep Diagnostics
  setText('val-voltage', `${volt.toFixed(1)} V`);
  setText('val-current', `${curr.toFixed(1)} A`);
  setText('val-power', `${power >= 0 ? '+' : ''}${power.toFixed(1)} kW`);
  setText('tpms-val-fl', `${psiFl.toFixed(0)} psi`);

  // Closed loop derate badge
  const derateBadge = document.getElementById('disp-derate-badge');
  if (derateBadge) {
    if (derate) {
      derateBadge.textContent = 'DERATE: ACTIVE (-50%)';
      derateBadge.className = 'derate-badge active';
    } else {
      derateBadge.textContent = 'DERATE: OFF';
      derateBadge.className = 'derate-badge';
    }
  }

  // TPMS FL Alert styling
  const wheelFl = document.getElementById('wheel-fl');
  if (wheelFl) {
    if (psiFl < 28) {
      wheelFl.classList.add('alert');
    } else {
      wheelFl.classList.remove('alert');
    }
  }

  // Automatic Acoustic & Visual Safety Alarm Triggering from Real-Time CAN Signals
  if (derate || !pumpOk || temp > 52 || psiFl < 26) {
    let msg = 'Vehicle telemetry outside nominal safety envelope.';
    let fType = 'coolant_pump';
    if (!pumpOk) {
      msg = 'Battery Coolant Pump Failure (DTC P0C73) — Pack Temperature Climbing! Emergency Safe Stop Engaged.';
      fType = 'coolant_pump';
    } else if (temp > 52) {
      msg = `Battery Pack Overtemperature (${temp.toFixed(1)}°C) — Closed-loop Emergency Safe Stop Engaged.`;
      fType = 'coolant_pump';
    } else if (psiFl < 26) {
      msg = `Rapid Tire Depressurization: ${psiFl.toFixed(0)} psi on Front-Left Tire (DTC C0035). Emergency Stop Engaged.`;
      fType = 'tpms_puncture';
    }
    if (!state.alarmActive) {
      triggerAlarm('CRITICAL VEHICLE SAFETY ALARM', msg);
    }
    if (!state.lastProactiveFault || state.lastProactiveFault !== fType) {
      state.lastProactiveFault = fType;
      triggerProactiveEmergencyChatbot(fType, msg);
    }
  } else if (state.activeFaults.size === 0 && state.alarmActive) {
    stopAlarm();
    state.lastProactiveFault = null;
  }

  // Longitudinal Physics Breakdown
  const v_ms = speed / 3.6;
  const f_aero = Math.round(0.5 * 1.2 * 0.72 * v_ms * v_ms);
  const f_roll = Math.round((0.010 * (1 + Math.max(0, 36.0 - psiFl) * 0.04)) * 1800 * 9.81);
  const hvac_kw = Number(data.ac_kw || (data.ambient_c && data.ambient_c > 35 ? 3.2 : 1.2)).toFixed(1);
  const regen_kw = power < 0 ? Math.abs(power).toFixed(1) : '0.0';

  setText('val-force-aero', `${f_aero} N`);
  setText('val-force-roll', `${f_roll} N`);
  setText('val-force-hvac', `${hvac_kw} kW`);
  setText('val-force-regen', `-${regen_kw} kW`);

  // Top Bar Trip Meta
  setText('disp-trip-badge', `TRIP #${trip} · ${road.toUpperCase()}`);

  // 2. Column 2: Center Speedometer Gauge
  setText('disp-speed', Math.round(speed));
  setText('disp-pwr-kw', `${power >= 0 ? '+' : ''}${power.toFixed(1)} kW`);
  setText('disp-soc-pct', `${Math.round(soc)}%`);

  const activeGaugePath = document.getElementById('speed-gauge-active');
  const needleGroup = document.getElementById('needle-group');
  const socBar = document.getElementById('soc-gauge-bar');
  const powerBar = document.getElementById('power-gauge-bar');

  if (activeGaugePath && needleGroup) {
    const maxSpeed = 220;
    const pct = Math.min(1, Math.max(0, speed / maxSpeed));
    // Dashoffset from 520 (0 km/h) to 0 (220 km/h)
    const offset = 520 - (pct * 520);
    activeGaugePath.style.strokeDashoffset = offset;

    // Needle rotates symmetrically from -110deg (0 km/h) to +110deg (220 km/h)
    const angle = -110 + (pct * 220);
    needleGroup.style.transform = `rotate(${angle}deg)`;
  }

  // Right Wing Battery SOC Bar (Vibrant Green)
  if (socBar) {
    const socPct = Math.min(100, Math.max(0, soc)) / 100;
    socBar.style.strokeDashoffset = 95 - (socPct * 95);
  }

  // Left Wing Motor Power Bar (Cyan)
  if (powerBar) {
    const pwrPct = Math.min(1, Math.max(0, (power + 20) / 100));
    powerBar.style.strokeDashoffset = 95 - (pwrPct * 95);
  }

  // Dynamic Gear Selector Strip
  updateGearStrip(speed);
}

function updateGearStrip(speed) {
  let activeGear = 'D4';
  if (speed < 0.5) activeGear = 'P';
  else if (speed < 20) activeGear = 'D1';
  else if (speed < 45) activeGear = 'D2';
  else if (speed < 80) activeGear = 'D3';
  else if (speed < 120) activeGear = 'D4';
  else activeGear = 'D5';

  document.querySelectorAll('.gear-slot').forEach(slot => {
    if (slot.getAttribute('data-gear') === activeGear) {
      slot.classList.add('active-gear');
    } else {
      slot.classList.remove('active-gear');
    }
  });
}

/* ==========================================================================
   3. Sensor Fault Injection & Closed-Loop Reaction Controls
   ========================================================================== */

function initFaultInjectionControls() {
  const faultButtons = document.querySelectorAll('.fault-btn');

  faultButtons.forEach(btn => {
    btn.addEventListener('click', async () => {
      const faultType = btn.getAttribute('data-fault');
      await executeFaultInjection(faultType, btn);
    });
  });
}

const EMERGENCY_CHAT_DATA = {
  coolant_pump: {
    code: 'DTC P0C73',
    title: {
      en: 'Battery Coolant Pump Failure (P0C73)',
      hi: 'बैटरी कूलेंट पंप खराबी (P0C73)',
      te: 'బ్యాటరీ కూలెంట్ పంప్ వైఫల్యం (P0C73)',
      ta: 'பேட்டரி குளிரூட்டும் பம்ப் செயலிழப்பு (P0C73)'
    },
    simple: {
      en: '⚠️ SUBSYSTEM WARNING: Battery coolant pump has stalled (DTC P0C73). Pack temperature is climbing. Closed-loop propulsion derate (-50%) is active to limit thermal stress. The vehicle continues cruising at a safe reduced speed without stopping.',
      hi: '⚠️ उपप्रणाली चेतावनी: बैटरी कूलेंट पंप बंद हो गया है (DTC P0C73)। हाई-वोल्टेज बैटरी तापमान बढ़ रहा है। 50% गति कमी सक्रिय है। वाहन सुरक्षित गति पर चलना जारी रखेगा।',
      te: '⚠️ సబ్‌సిస్టమ్ హెచ్చరిక: బ్యాటరీ కూలెంట్ పంప్ ఆగిపోయింది (DTC P0C73). ఉష్ణోగ్రత పెరుగుతోంది. 50% వేగం తగ్గింపు అమల్లో ఉంది. వాహనం సురక్షితంగా ప్రయాణం కొనసాగిస్తుంది.',
      ta: '⚠️ எச்சரிக்கை: பேட்டரி கூலன்ட் பம்ப் செயலிழந்தது (DTC P0C73). 50% வேகக் குறைப்பு செயல்படுத்தப்பட்டுள்ளது. வாகனம் பாதுகாப்பாக தொடர்ந்து இயங்கும்.'
    },
    detailed: '• Subsystem: High-Voltage BMS Thermal Management Loop\n• Confirmed DTC: P0C73 (Battery Coolant Pump Performance)\n• Telemetry: Coolant flow rate 0.0 L/min, cell thermal gradient rising\n• Propulsion Impact: Closed-loop power derate (-50%), vehicle cruising smoothly under reduced power without stopping\n• Recommended Action: Schedule coolant pump inspection or clear fault.',
    chips: [
      { label: '🔍 Why did this happen?', query: 'What happened before the battery overheating warning?' },
      { label: '📊 Check Battery Status', query: 'What is the current status of my vehicle?' },
      { label: '💡 How to fix overheating?', query: 'Why is my battery overheating and how to prevent damage?' },
      { label: '🟢 Clear Fault & Restore Power', action: 'clear' }
    ]
  },
  tpms_puncture: {
    code: 'DTC C0035',
    title: {
      en: 'Front-Left Tire Puncture (18 PSI)',
      hi: 'अगला बायां टायर पंचर (18 PSI)',
      te: 'ముందు ఎడమ టైర్ పంక్చర్ (18 PSI)',
      ta: 'முன் இடது டயர் பஞ்சர் (18 PSI)'
    },
    simple: {
      en: '⚠️ TPMS WARNING: Front-left tire pressure dropped to 18 PSI (DTC C0035). Rolling resistance has surged +45% (290 N). The vehicle continues driving with increased tire drag.',
      hi: '⚠️ टायर प्रेशर चेतावनी: अगला बायां टायर प्रेशर 18 PSI तक गिर गया (DTC C0035)। रोलिंग प्रतिरोध 45% बढ़ गया है। वाहन चलना जारी रखेगा।',
      te: '⚠️ టైర్ ప్రెషర్ హెచ్చరిక: ముందు ఎడమ టైర్ ప్రెషర్ 18 PSIకి పడిపోయింది. రోలింగ్ రెసిస్టెన్స్ పెరిగింది. వాహనం ప్రయాణం కొనసాగిస్తుంది.',
      ta: '⚠️ டயர் எச்சரிக்கை: முன் இடது டயர் அழுத்தம் 18 PSI ஆக குறைந்தது. வாகனம் தொடர்ந்து இயங்கும்.'
    },
    detailed: '• Subsystem: Tire Pressure Monitoring System (TPMS) & Chassis Dynamics\n• Confirmed DTC: C0035 (FL Pressure Low Rapid Loss)\n• Telemetry: FL Tire Pressure 18.0 PSI vs 36.0 PSI nominal (Rolling force 290 N)\n• Dynamics Impact: Vehicle continues cruising with increased rolling resistance\n• Recommended Action: Re-inflate tire, inspect puncture valve, or clear fault.',
    chips: [
      { label: '🔍 Explain Tire Depressurization', query: 'Why did my tire pressure drop and how does it affect range?' },
      { label: '📊 Check Chassis Health', query: 'What is the current status of my vehicle?' },
      { label: '🟢 Clear Fault & Restore Pressure', action: 'clear' }
    ]
  },
  inverter: {
    code: 'DTC P0A0F',
    title: {
      en: 'Motor Inverter Phase Overheat (>110°C)',
      hi: 'मोटर इन्वर्टर अधिक गर्म (>110°C)',
      te: 'మోటార్ ఇన్వర్టర్ ఓవర్‌హీట్ (>110°C)',
      ta: 'மோட்டார் இன்வெர்ட்டர் அதிக வெப்பம் (>110°C)'
    },
    simple: {
      en: '⚠️ INVERTER WARNING: Motor inverter phase temperature exceeded 110°C (DTC P0A0F). Drive torque is derated to prevent silicon power semiconductor damage. Vehicle continues driving under reduced power.',
      hi: '⚠️ इन्वर्टर चेतावनी: मोटर इन्वर्टर तापमान 110°C से अधिक हो गया है (DTC P0A0F)। टॉर्क सीमित कर दिया गया है। वाहन कम पावर पर चलना जारी रखेगा।',
      te: '⚠️ ఇన్వర్టర్ హెచ్చరిక: మోటార్ ఇన్వర్టర్ ఉష్ణోగ్రత 110°C దాటింది (DTC P0A0F). టార్క్ పరిమితం చేయబడింది. వాహనం ప్రయాణం కొనసాగిస్తుంది.',
      ta: '⚠️ இன்வெர்ட்டர் எச்சரிக்கை: மோட்டார் இன்வெர்ட்டர் வெப்பநிலை 110°C ஐ தாண்டியது (DTC P0A0F). வாகனம் தொடர்ந்து இயங்கும்.'
    },
    detailed: '• Subsystem: Power Electronics PCU / Traction Inverter\n• Confirmed DTC: P0A0F (Inverter Phase Thermal Limit Exceeded)\n• Telemetry: Inverter Silicon Temp > 110°C, phase switching efficiency reduced\n• Propulsion Impact: Torque limited; vehicle cruising steadily\n• Recommended Action: Allow thermal cooldown, check inverter coolant loop, or clear fault.',
    chips: [
      { label: '🔍 Explain Inverter Overheat', query: 'What caused the motor inverter overtemperature warning?' },
      { label: '📊 Check Power Electronics', query: 'What is the current status of my vehicle?' },
      { label: '🟢 Clear Fault & Restore Torque', action: 'clear' }
    ]
  },
  adas_occlusion: {
    code: 'DTC U0100',
    title: {
      en: 'ADAS Sensor Occlusion / Takeover Notice',
      hi: 'एडीएएस सेंसर अवरोध / टेकओवर सूचना',
      te: 'ADAS సెన్సార్ అస్పష్టత / టేకోవర్ గమనిక',
      ta: 'ADAS சென்சார் பார்வை தடை / அறிவிப்பு'
    },
    simple: {
      en: '⚠️ ADAS WARNING: Front optical camera and radar sensors are occluded by glare/dirt (DTC U0100). Lane Keep Assist disengaged. Vehicle continues driving under manual/traffic manager cruising.',
      hi: '⚠️ एडीएएस चेतावनी: कैमरा और रडार सेंसर दृष्टि अवरुद्ध हो गई है (DTC U0100)। लेन कीप असिस्ट बंद हो गया है। वाहन चलना जारी रखेगा।',
      te: '⚠️ ADAS హెచ్చరిక: కెమెరా మరియు రాడార్ సెన్సార్ల దృష్టి అస్పష్టంగా ఉంది (DTC U0100). లేన్ కీప్ నిలిపివేయబడింది. వాహనం ప్రయాణం కొనసాగిస్తుంది.',
      ta: '⚠️ ADAS எச்சரிக்கை: கேமரா மற்றும் ரேடார் பார்வை தடைபட்டது (DTC U0100). வாகனம் தொடர்ந்து இயங்கும்.'
    },
    detailed: '• Subsystem: ADAS Perception Suite (Mobileye Camera + Continental Radar)\n• Confirmed DTC: U0100 (Sensor Vision Occlusion / Blind Zone)\n• Telemetry: SOME/IP 0x4ADA service event disengaged (reason: RADAR_CAMERA_OCCLUSION)\n• Control Impact: Autopilot cruising active, optical steering assistive features disengaged\n• Recommended Action: Clean front windshield camera housing and radar radome, or clear fault.',
    chips: [
      { label: '🔍 Why did ADAS disengage?', query: 'Why did the autonomous driving system disengage?' },
      { label: '📊 Check ADAS Status', query: 'What is the current status of my vehicle?' },
      { label: '🟢 Clear Fault & Re-engage ADAS', action: 'clear' }
    ]
  },
  ambient_heat: {
    code: 'HVAC-42C',
    title: {
      en: 'Extreme Heatwave (42°C Ambient)',
      hi: 'भीषण गर्मी (42°C परिवेशी तापमान)',
      te: 'విపరీతమైన ఎండవేడిమి (42°C వాతావరణం)',
      ta: 'கடுமையான வெப்ப அலை (42°C)'
    },
    simple: {
      en: '☀️ THERMAL STRESS ALERT: Extreme ambient temperature 42°C detected. Cabin AC compressor power elevated to 3.2 kW, causing higher auxiliary battery drain. Vehicle continues driving with lower estimated range.',
      hi: '☀️ थर्मल तनाव चेतावनी: 42°C अत्यधिक परिवेश तापमान का पता चला है। केबिन एसी कंप्रेसर 3.2 kW पर चल रहा है। वाहन चलना जारी रखेगा।',
      te: '☀️ థర్మల్ హెచ్చరిక: 42°C విపరీతమైన వాతావరణ ఉష్ణోగ్రత నమోదైంది. క్యాబిన్ AC 3.2 kW పవర్ వినియోగిస్తోంది. వాహనం ప్రయాణం కొనసాగిస్తుంది.',
      ta: '☀️ வெப்ப எச்சரிக்கை: 42°C கடுமையான சுற்றுப்புற வெப்பநிலை. ஏசி சுமை 3.2 kW ஆக உயர்ந்துள்ளது. வாகனம் தொடர்ந்து இயங்கும்.'
    },
    detailed: '• Subsystem: Thermal Management & HVAC Compression\n• Telemetry: Ambient 42.0°C, AC power draw 3.20 kW, cabin setpoint 22.0°C\n• Physics Impact: +2.0 kW auxiliary battery load, reducing full charge range by 18%\n• Propulsion Impact: Vehicle cruising normally under elevated auxiliary electrical load\n• Recommended Action: Pre-condition cabin in shade or clear heatwave.',
    chips: [
      { label: '🔍 How does AC affect range?', query: 'Why is my driving range dropping faster than expected?' },
      { label: '📊 Check Energy Breakdown', query: 'What is the current status of my vehicle?' },
      { label: '🟢 Clear Heatwave & Restore Range', action: 'clear' }
    ]
  },
  all_faults: {
    code: 'CASCADE-ALL-DTCs',
    title: {
      en: 'Total System Cascade Failure — All Faults Triggered',
      hi: 'संपूर्ण प्रणाली विफलता — सभी दोष सक्रिय',
      te: 'మొత్తం వ్యవస్థ వైఫల్యం — అన్ని లోపాలు యాక్టివేట్ అయ్యాయి',
      ta: 'முழு அமைப்பு செயலிழப்பு — அனைத்து குறைபாடுகளும் தூண்டப்பட்டன'
    },
    simple: {
      en: '🚨 TOTAL SYSTEM CASCADE FAILURE: All major vehicle subsystems have faulted simultaneously (Battery Coolant Pump P0C73, FL Tire Puncture C0035, Motor Inverter P0A0F, ADAS Vision Blind U0100, and Ambient Heatwave 42°C). The autonomous safety supervisor has commanded an IMMEDIATE EMERGENCY SAFE STOP (0.0 km/h) in the CARLA simulation. Vehicle is fully stopped with brakes locked.',
      hi: '🚨 संपूर्ण प्रणाली विफलता: वाहन के सभी प्रमुख घटक एक साथ विफल हो गए हैं (बैटरी कूलेंट पंप P0C73, टायर पंचर C0035, मोटर इन्वर्टर P0A0F, एडीएएस दृष्टि U0100, और 42°C गर्मी)। ऑटोनॉमस सुरक्षा प्रणाली ने सिमुलेशन में वाहन को तुरंत पूरी तरह सुरक्षित रोक दिया है (0.0 km/h)। ब्रेक लॉक हैं।',
      te: '🚨 మొత్తం వ్యవస్థ వైఫల్యం: వాహనం యొక్క అన్ని ప్రధాన వ్యవస్థలు ఏకకాలంలో విఫలమయ్యాయి (బ్యాటరీ కూలెంట్ పంప్ P0C73, టైర్ పంక్చర్ C0035, మోటార్ ఇన్వర్టర్ P0A0F, ADAS బ్లైండ్ U0100, మరియు 42°C ఎండవేడిమి). వాహనాన్ని సిమ్యులేషన్‌లో తక్షణమే పూర్తిగా ఆపడం జరిగింది (0.0 km/h). బ్రేకులు లాక్ చేయబడ్డాయి.',
      ta: '🚨 முழு அமைப்பு செயலிழப்பு: அனைத்து முக்கிய வாகன அமைப்புகளும் ஒரே நேரத்தில் செயலிழந்தன (பேட்டரி கூலன்ட் பம்ப் P0C73, டயர் பஞ்சர் C0035, இன்வெர்ட்டர் P0A0F, ADAS U0100, மற்றும் வெப்ப அலை 42°C). விபத்தைத் தவிர்க்க சிமுலேஷனில் வாகனம் உடனடியாக முழுமையாக நிறுத்தப்பட்டுள்ளது (0.0 km/h).'
    },
    detailed: '• Subsystems: High-Voltage BMS Thermal Loop, TPMS Chassis, PCU Inverter, ADAS Perception, Thermal HVAC\n• Active DTCs: P0C73 (BMS Thermal), C0035 (TPMS 18 psi), P0A0F (Inverter 110°C), U0100 (Vision Occlusion)\n• Simulation Status: Autopilot disengaged, maximum mechanical brake & handbrake locked (Speed: 0.0 km/h)\n• Autonomous Safe Protocol: Minimum Risk Condition (MRC) achieved. Clear faults to resume drive.',
    chips: [
      { label: '🔍 Why did all faults happen?', query: 'What happened before the battery overheating warning?' },
      { label: '📊 Complete Vehicle Health', query: 'What is the current status of my vehicle?' },
      { label: '💡 Explain Powertrain Stress', query: 'Why is my driving range dropping faster than expected?' },
      { label: '🟢 Clear All Faults & Resume Drive', action: 'clear' }
    ]
  }
};

function triggerProactiveEmergencyChatbot(faultType, customDetail) {
  const stream = document.getElementById('chat-messages');
  if (!stream) return;

  const data = EMERGENCY_CHAT_DATA[faultType] || EMERGENCY_CHAT_DATA.coolant_pump;
  const lang = state.lang || 'en';
  const simpleText = (data.simple && data.simple[lang]) || data.simple.en;
  const titleText = (data.title && data.title[lang]) || data.title.en;

  const isCascadeStop = (faultType === 'all_faults');
  const statusBadge = isCascadeStop ? '· EMERGENCY SAFE STOP (0.0 km/h)' : '· ACTIVE WARNING (VEHICLE CRUISING)';
  const icon = isCascadeStop ? '🚨' : '⚠️';

  const bubble = document.createElement('div');
  bubble.className = 'chat-bubble bot-bubble emergency-dialogue-bubble';

  const card = document.createElement('div');
  card.className = 'emergency-dialogue-card';

  card.innerHTML = `
    <div class="emergency-card-header">
      <div class="emergency-card-title">
        <span style="font-size:1.05rem;">${icon}</span>
        <span>${titleText.toUpperCase()}</span>
      </div>
      <div style="display:flex;align-items:center;gap:6px;">
        <span class="emergency-code-pill">${data.code}</span>
        <button class="mini-tts-btn" style="background:transparent;border:none;cursor:pointer;color:#EF4444;font-size:0.85rem;" title="Listen to Alert">🔊</button>
      </div>
    </div>
    <div class="simple-summary-card" style="background:${isCascadeStop ? 'rgba(239, 68, 68, 0.16)' : 'rgba(234, 179, 8, 0.12)'};border-color:${isCascadeStop ? 'rgba(239, 68, 68, 0.5)' : 'rgba(234, 179, 8, 0.35)'};margin-bottom:6px;">
      <div class="simple-summary-header" style="color:${isCascadeStop ? '#FCA5A5' : '#FDE047'};">
        <span>💡 DRIVER SAFETY GLANCE (${lang.toUpperCase()}) ${statusBadge}</span>
      </div>
      <div class="simple-summary-text" style="color:#FFF;">${simpleText}</div>
    </div>
    <div class="detailed-accordion">
      <div class="detailed-toggle-header">
        <span>🔬 SUBSYSTEM TELEMETRY &amp; ROOT-CAUSE FACTS</span>
        <span class="chevron">▼</span>
      </div>
      <div class="detailed-body hidden">
        <div class="fact-answer-box" style="white-space:pre-line;font-size:0.68rem;line-height:1.45;color:#E2E8F0;">
          ${data.detailed}
        </div>
      </div>
    </div>
    <div class="emergency-action-chips"></div>
  `;

  const header = card.querySelector('.detailed-toggle-header');
  const body = card.querySelector('.detailed-body');
  header.addEventListener('click', () => {
    header.classList.toggle('open');
    body.classList.toggle('hidden');
    stream.scrollTop = stream.scrollHeight;
  });

  const ttsBtn = card.querySelector('.mini-tts-btn');
  ttsBtn.onclick = () => speakText(cleanTextForTTS(simpleText));

  const chipsContainer = card.querySelector('.emergency-action-chips');
  data.chips.forEach(chip => {
    const chipBtn = document.createElement('button');
    chipBtn.className = `emergency-action-chip ${chip.action === 'clear' ? 'clear-chip' : ''}`;
    chipBtn.textContent = chip.label;
    chipBtn.addEventListener('click', () => {
      if (chip.action === 'clear') {
        executeFaultInjection('clear');
      } else if (chip.query) {
        handleUserQuery(chip.query);
      }
    });
    chipsContainer.appendChild(chipBtn);
  });

  bubble.appendChild(card);
  stream.appendChild(bubble);
  stream.scrollTop = stream.scrollHeight;

  if (state.ttsEnabled) {
    speakText(cleanTextForTTS(simpleText));
  }
}

function triggerProactiveResumeChatbot() {
  const stream = document.getElementById('chat-messages');
  if (!stream) return;

  const lang = state.lang || 'en';
  const resumeTexts = {
    en: "✅ <b>Nominal Baselines Restored!</b> All faults cleared (DTCs P0C73/C0035 reset). Mechanical brakes released, Autopilot re-engaged. Vehicle is resuming drive in the CARLA simulation.",
    hi: "✅ <b>दोष साफ़ हो गए!</b> सभी डीटीसी साफ़ कर दिए गए हैं। ब्रेक हटा दिए गए हैं और ऑटोपायलट फिर से सक्रिय हो गया है। वाहन सिमुलेशन में फिर से चलना शुरू कर रहा है।",
    te: "✅ <b>లోపాలు క్లియర్ చేయబడ్డాయి!</b> బ్రేకులు విడుదల చేయబడ్డాయి మరియు ఆటోపైలట్ పునఃప్రారంభించబడింది. వాహనం సిమ్యులేషన్‌లో డ్రైవింగ్ పునఃప్రారంభిస్తోంది.",
    ta: "✅ <b>அனைத்து குறைபாடுகளும் நீக்கப்பட்டன!</b> பிரேக்குகள் விடுவிக்கப்பட்டு ஆட்டோபைலட் மீண்டும் இயக்கப்பட்டது. வாகனம் சிமுலேஷனில் தனது பயணத்தைத் தொடர்கிறது."
  };
  const resumeText = resumeTexts[lang] || resumeTexts.en;

  const bubble = document.createElement('div');
  bubble.className = 'chat-bubble bot-bubble';

  const card = document.createElement('div');
  card.className = 'resume-dialogue-card';
  card.innerHTML = `
    <div class="resume-card-header">
      <span>🟢 SAFETY SYSTEMS NOMINAL · AUTONOMOUS DRIVE RESUMED</span>
      <button class="mini-tts-btn" style="background:transparent;border:none;cursor:pointer;color:#10B981;font-size:0.85rem;" title="Listen">🔊</button>
    </div>
    <div style="font-size:0.72rem;line-height:1.45;color:#F8FAFC;">${resumeText}</div>
  `;

  const ttsBtn = card.querySelector('.mini-tts-btn');
  ttsBtn.onclick = () => speakText(cleanTextForTTS(resumeText));

  bubble.appendChild(card);
  stream.appendChild(bubble);
  stream.scrollTop = stream.scrollHeight;

  if (state.ttsEnabled) {
    speakText(cleanTextForTTS(resumeText));
  }
}

async function executeFaultInjection(faultType, buttonEl) {
  const tickerEl = document.getElementById('ticker-text');

  if (faultType === 'clear') {
    state.activeFaults.clear();
    state.lastProactiveFault = null;
    stopAlarm();
    document.querySelectorAll('.fault-btn').forEach(b => b.classList.remove('active-fault'));

    if (tickerEl) tickerEl.textContent = 'All faults cleared. Restoring nominal ECU baselines (P0C73, C0035 cleared).';
    triggerProactiveResumeChatbot();

    await fetch('/api/control', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ cmd: 'clear' })
    });
    return;
  }

  if (faultType === 'all_faults') {
    const allTypes = ['coolant_pump', 'tpms_puncture', 'ambient_heat', 'inverter', 'adas_occlusion', 'all_faults'];
    allTypes.forEach(f => state.activeFaults.add(f));
    document.querySelectorAll('.fault-btn').forEach(b => {
      if (b.getAttribute('data-fault') !== 'clear') {
        b.classList.add('active-fault');
      }
    });

    const tickerMsg = 'CASCADE FAILURE: All subsystems faulted (P0C73, C0035, P0A0F, U0100, 42°C Heat). EMERGENCY SAFE STOP ENGAGED (0.0 km/h).';
    if (tickerEl) tickerEl.textContent = tickerMsg;

    triggerAlarm('TOTAL SYSTEM CASCADE FAILURE', tickerMsg);
    triggerProactiveEmergencyChatbot('all_faults', tickerMsg);

    await fetch('/api/control', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ cmd: 'all_faults' })
    });
    return;
  }

  // Toggle active fault
  const isActive = state.activeFaults.has(faultType);
  if (isActive) {
    state.activeFaults.delete(faultType);
    state.activeFaults.delete('all_faults');
    const allBtn = document.getElementById('btn-fault-all');
    if (allBtn) allBtn.classList.remove('active-fault');
    if (buttonEl) buttonEl.classList.remove('active-fault');
  } else {
    state.activeFaults.add(faultType);
    if (buttonEl) buttonEl.classList.add('active-fault');
  }

  let payload = {};
  let tickerMsg = '';

  switch (faultType) {
    case 'coolant_pump':
      payload = { cmd: 'fault', name: 'coolant_pump', accel: 9.0 };
      tickerMsg = 'DTC P0C73 SET: Battery coolant pump performance failure. Thermal runaway protection -> Emergency Safe Stop.';
      break;

    case 'tpms_puncture':
      payload = { cmd: 'tire_leak', psi: 18.0 };
      tickerMsg = 'DTC C0035 SET: Front-left TPMS rapid air loss (18 psi). Rolling resistance force increased +45% -> Emergency Safe Stop.';
      break;

    case 'ambient_heat':
      payload = { cmd: 'ambient', value: 42.0, ac: 3.2 };
      tickerMsg = 'HVAC STRESS: Ambient heatwave 42°C detected. Cabin AC compressor load elevated to 3.2 kW -> Emergency Safe Stop.';
      break;

    case 'inverter':
      payload = { cmd: 'fault', name: 'inverter' };
      tickerMsg = 'DTC P0A0F SET: Motor inverter phase temperature exceeded 110°C. Inverter efficiency reduced -> Emergency Safe Stop.';
      break;

    case 'adas_occlusion':
      payload = { cmd: 'adas_disengage', reason: 'RADAR_CAMERA_OCCLUSION' };
      tickerMsg = 'DTC U0100 SET: Camera and radar sensor blind zone / glare. ADAS disengaged -> Takeover Emergency Safe Stop.';
      break;
  }

  if (tickerEl) tickerEl.textContent = tickerMsg;

  if (state.activeFaults.has(faultType)) {
    const isAllActive = ['coolant_pump', 'tpms_puncture', 'ambient_heat', 'inverter', 'adas_occlusion'].every(f => state.activeFaults.has(f));
    if (isAllActive) {
      state.activeFaults.add('all_faults');
      const allBtn = document.getElementById('btn-fault-all');
      if (allBtn) allBtn.classList.add('active-fault');
      triggerAlarm('TOTAL SYSTEM CASCADE FAILURE', 'All subsystems faulted. EMERGENCY SAFE STOP ENGAGED.');
      triggerProactiveEmergencyChatbot('all_faults', 'All subsystems faulted. EMERGENCY SAFE STOP ENGAGED.');
    } else {
      triggerAlarm('CRITICAL VEHICLE FAULT INJECTED', tickerMsg);
      triggerProactiveEmergencyChatbot(faultType, tickerMsg);
    }
  } else if (state.activeFaults.size === 0) {
    stopAlarm();
    state.lastProactiveFault = null;
    triggerProactiveResumeChatbot();
    await fetch('/api/control', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ cmd: 'clear' })
    });
    return;
  }

  await fetch('/api/control', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload)
  });
}

/* ==========================================================================
   4. CARLA Controls (City Map, Weather, Views, Autopilot)
   ========================================================================== */

function initCarlaControls() {
  const citySelect = document.getElementById('carla-city-select');
  const weatherSelect = document.getElementById('carla-weather-select');
  const viewSelect = document.getElementById('carla-view-select');
  const apBtn = document.getElementById('btn-toggle-ap');
  const cityBadge = document.getElementById('cam-active-city-badge');

  if (citySelect) {
    citySelect.addEventListener('change', async (e) => {
      const selectedTown = e.target.value;
      state.town = selectedTown;
      if (cityBadge) cityBadge.textContent = `${selectedTown}`;
      addCopilotMessage(`🌐 <b>Switching CARLA city map to ${selectedTown}...</b> Spawning vehicle in new world.`);

      await fetch('/api/carla/control', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ town: selectedTown })
      });
    });
  }

  if (weatherSelect) {
    weatherSelect.addEventListener('change', async (e) => {
      const wPreset = e.target.value;
      state.weather = wPreset;
      addCopilotMessage(`⛅ <b>Changing CARLA Weather to ${wPreset}...</b>`);
      await fetch('/api/carla/control', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ weather: wPreset })
      });
    });
  }

  if (viewSelect) {
    viewSelect.addEventListener('change', async (e) => {
      const view = e.target.value;
      state.camView = view;
      await fetch('/api/carla/control', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ camera: view })
      });
    });
  }

  if (apBtn) {
    apBtn.addEventListener('click', async () => {
      state.autopilot = !state.autopilot;
      apBtn.textContent = state.autopilot ? '● AUTOPILOT ON' : '○ MANUAL DRIVE';
      apBtn.className = state.autopilot ? 'cam-btn ap-btn' : 'cam-btn';
      await fetch('/api/carla/control', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ autopilot: state.autopilot })
      });
    });
  }
}

function handleStreamError(img) {
  setTimeout(() => {
    img.src = `/api/carla/stream?t=${Date.now()}`;
  }, 4000);
}

function handleStreamLoaded(img) {}

/* ==========================================================================
   5. Dedicated VehicleGPT AI Copilot (Ollama Qwen 1.5B + Deterministic Facts)
   ========================================================================== */

function initCopilotAssistant() {
  // Mode Tabs (Simple Driver Glance vs Detailed Engineering)
  const btnSimple = document.getElementById('btn-mode-simple');
  const btnDetailed = document.getElementById('btn-mode-detailed');
  if (btnSimple && btnDetailed) {
    btnSimple.addEventListener('click', () => {
      state.viewMode = 'simple';
      btnSimple.classList.add('active');
      btnDetailed.classList.remove('active');
      addCopilotMessage('💡 Switched to <b>Simple Driver Glance Mode</b>.');
    });
    btnDetailed.addEventListener('click', () => {
      state.viewMode = 'detailed';
      btnDetailed.classList.add('active');
      btnSimple.classList.remove('active');
      addCopilotMessage('🔬 Switched to <b>Deep Engineering Diagnostics Mode</b>.');
    });
  }

  // Language Tabs
  document.querySelectorAll('.lang-tab').forEach(tab => {
    tab.addEventListener('click', () => {
      document.querySelectorAll('.lang-tab').forEach(t => t.classList.remove('active'));
      tab.classList.add('active');
      state.lang = tab.getAttribute('data-lang');
      addCopilotMessage(`Language set to: <b>${tab.textContent}</b>`);
    });
  });

  // Role Selector
  const roleSelect = document.getElementById('role-select');
  if (roleSelect) {
    roleSelect.addEventListener('change', (e) => {
      state.role = e.target.value;
      addCopilotMessage(`Persona role updated to: <b>${roleSelect.options[roleSelect.selectedIndex].text}</b>`);
    });
  }

  // The 5 Exact Questions from Tata Elxsi Case Study 5 PDF
  document.querySelectorAll('.pdf-chip, .quick-pill').forEach(chip => {
    chip.addEventListener('click', () => {
      const q = chip.getAttribute('data-query');
      handleUserQuery(q);
    });
  });

  // Chat Input
  const chatInput = document.getElementById('chat-text-input');
  const sendBtn = document.getElementById('btn-chat-send');

  if (sendBtn && chatInput) {
    sendBtn.addEventListener('click', () => handleUserQuery());
    chatInput.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') handleUserQuery();
    });
  }
}

function handleUserQuery(text) {
  const chatInput = document.getElementById('chat-text-input');
  const q = text || (chatInput ? chatInput.value.trim() : '');
  if (!q) return;
  if (chatInput) chatInput.value = '';

  addUserMessage(q);
  executeVehicleGPTQuery(q);
}

function addUserMessage(text) {
  const stream = document.getElementById('chat-messages');
  if (!stream) return;

  const bubble = document.createElement('div');
  bubble.className = 'chat-bubble user-bubble';
  bubble.textContent = text;
  stream.appendChild(bubble);
  stream.scrollTop = stream.scrollHeight;
}

function addCopilotMessage(html) {
  const stream = document.getElementById('chat-messages');
  if (!stream) return;

  const bubble = document.createElement('div');
  bubble.className = 'chat-bubble bot-bubble';
  bubble.innerHTML = html;
  stream.appendChild(bubble);
  stream.scrollTop = stream.scrollHeight;
}

async function executeVehicleGPTQuery(question) {
  const stream = document.getElementById('chat-messages');
  if (!stream) return;

  // Bot response container
  const bubble = document.createElement('div');
  bubble.className = 'chat-bubble bot-bubble';
  
  const meta = document.createElement('div');
  meta.className = 'bubble-meta';
  meta.innerHTML = `
    <span class="bubble-sender">VEHICLEGPT AI (${state.lang.toUpperCase()})</span>
    <span class="bubble-tag">${state.role.toUpperCase()} · &lt;15ms FACTS</span>
  `;
  bubble.appendChild(meta);

  // 1. Simple Driver Summary Card
  const simpleCard = document.createElement('div');
  simpleCard.className = 'simple-summary-card';
  simpleCard.innerHTML = `
    <div class="simple-summary-header">
      <span>💡 QUICK DRIVER SUMMARY (${state.lang.toUpperCase()})</span>
      <button class="mini-tts-btn" style="background:transparent;border:none;cursor:pointer;color:#38BDF8;font-size:0.8rem;" title="Listen">🔊</button>
    </div>
    <div class="simple-summary-text"><em>Computing plain-language driver takeaway...</em></div>
  `;
  bubble.appendChild(simpleCard);

  // 2. Detailed Diagnostics Accordion (Ground Truth + Physics + SLM Stream)
  const detailedAccordion = document.createElement('div');
  detailedAccordion.className = 'detailed-accordion';
  const isOpen = state.viewMode === 'detailed' || state.role === 'service' || state.role === 'fleet';
  detailedAccordion.innerHTML = `
    <div class="detailed-toggle-header ${isOpen ? 'open' : ''}">
      <span>🔬 DETAILED ENGINEERING &amp; GROUND-TRUTH FACTS</span>
      <span class="chevron">▼</span>
    </div>
    <div class="detailed-body ${isOpen ? '' : 'hidden'}">
      <div class="fact-answer-box"><em>Retrieving deterministic CAN telemetry ground-truth...</em></div>
      <div class="slm-response-text" style="margin-top:6px;line-height:1.4;color:#CBD5E1;"></div>
    </div>
  `;
  bubble.appendChild(detailedAccordion);

  // Toggle behavior
  const header = detailedAccordion.querySelector('.detailed-toggle-header');
  const body = detailedAccordion.querySelector('.detailed-body');
  header.addEventListener('click', () => {
    header.classList.toggle('open');
    body.classList.toggle('hidden');
    stream.scrollTop = stream.scrollHeight;
  });

  const factBox = detailedAccordion.querySelector('.fact-answer-box');
  const textContainer = detailedAccordion.querySelector('.slm-response-text');
  const simpleTextEl = simpleCard.querySelector('.simple-summary-text');
  const simpleTtsBtn = simpleCard.querySelector('.mini-tts-btn');

  stream.appendChild(bubble);
  stream.scrollTop = stream.scrollHeight;

  try {
    const sseUrl = `/api/explain_stream?question=${encodeURIComponent(question)}&role=${encodeURIComponent(state.role)}&lang=${encodeURIComponent(state.lang)}&mode=${encodeURIComponent(state.viewMode)}`;
    const eventSource = new EventSource(sseUrl);
    let fullText = '';
    let currentSimpleText = '';

    eventSource.onmessage = (event) => {
      const data = JSON.parse(event.data);

      if (data.type === 'facts') {
        const f = data.data;
        const sums = f.summaries || {};
        currentSimpleText = (sums.simple && sums.simple[state.lang]) || f.simple_text || f.text;
        simpleTextEl.textContent = currentSimpleText;

        simpleTtsBtn.onclick = () => speakText(currentSimpleText);

        let decompHtml = '';
        if (f.causes) {
          const aero = Math.round((f.causes.aero_drag || 0.42) * 100);
          const roll = Math.round((f.causes.rolling_resistance || 0.28) * 100);
          const hvac = Math.round((f.causes.hvac_thermal || 0.18) * 100);
          const regen = Math.max(0, 100 - aero - roll - hvac);

          decompHtml = `
            <div class="physics-decomp-bar">
              <div class="decomp-seg seg-aero" style="width: ${aero}%;" title="Aero Drag: ${aero}%"></div>
              <div class="decomp-seg seg-roll" style="width: ${roll}%;" title="Rolling Res: ${roll}%"></div>
              <div class="decomp-seg seg-hvac" style="width: ${hvac}%;" title="HVAC: ${hvac}%"></div>
              <div class="decomp-seg seg-regen" style="width: ${regen}%;" title="Regen: ${regen}%"></div>
            </div>
            <div class="physics-legend">
              <span>🔵 Aero: ${aero}%</span>
              <span>🟠 Rolling: ${roll}%</span>
              <span>🔴 HVAC: ${hvac}%</span>
              <span>🟢 Regen: ${regen}%</span>
            </div>
          `;
        }

        const lat = f.timings ? f.timings.total_ms.toFixed(1) : '<15';
        factBox.innerHTML = `
          <div class="fact-title-row">
            <span>DETERMINISTIC GROUND TRUTH (SQL + PHYSICS)</span>
            <span>⚡ ${lat} ms</span>
          </div>
          <div style="font-size:0.68rem;line-height:1.4;">${f.text || 'Telemetry ground truth verified.'}</div>
          ${decompHtml}
        `;
      } else if (data.type === 'token') {
        fullText += data.token;
        textContainer.innerHTML = fullText.replace(/\n/g, '<br>');
        stream.scrollTop = stream.scrollHeight;
      } else if (data.type === 'done') {
        eventSource.close();
        if (state.ttsEnabled && 'speechSynthesis' in window) {
          speakText(cleanTextForTTS(state.viewMode === 'simple' && currentSimpleText ? currentSimpleText : fullText));
        }
      }
    };

    eventSource.onerror = async () => {
      eventSource.close();
      const res = await fetch('/api/ask', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ question, role: state.role, lang: state.lang })
      });
      const json = await res.json();
      const s = json.summaries || {};
      const fallbackSimple = (s.simple && s.simple[state.lang]) || json.simple_text || json.text;
      simpleTextEl.textContent = fallbackSimple;
      simpleTtsBtn.onclick = () => speakText(fallbackSimple);
      factBox.innerHTML = `<b>Ground Truth:</b> ${json.text}`;
      textContainer.textContent = json.text;
      if (state.ttsEnabled) speakText(cleanTextForTTS(fallbackSimple));
    };

  } catch (err) {
    simpleTextEl.textContent = `Error: ${err.message}`;
    factBox.innerHTML = `Error contacting VehicleGPT: ${err.message}`;
  }
}

/* ==========================================================================
   6. Speech Recognition (Mic Input) & Speech Synthesis (Voice Readback)
   ========================================================================= */

function initSpeechFeatures() {
  const micBtn = document.getElementById('btn-voice-input');
  const ttsBtn = document.getElementById('btn-tts-toggle');

  if (ttsBtn) {
    ttsBtn.addEventListener('click', () => {
      state.ttsEnabled = !state.ttsEnabled;
      ttsBtn.classList.toggle('active', state.ttsEnabled);
      addCopilotMessage(state.ttsEnabled ? '🔊 <b>Voice readback enabled.</b>' : '🔇 <b>Voice readback muted.</b>');
    });
  }

  if (micBtn && ('webkitSpeechRecognition' in window || 'SpeechRecognition' in window)) {
    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    const recognizer = new SpeechRecognition();
    recognizer.continuous = false;
    recognizer.interimResults = false;

    micBtn.addEventListener('click', () => {
      if (state.speechActive) {
        recognizer.stop();
        return;
      }

      // Map language code
      const langMap = { en: 'en-IN', hi: 'hi-IN', te: 'te-IN', ta: 'ta-IN' };
      recognizer.lang = langMap[state.lang] || 'en-IN';
      
      try {
        recognizer.start();
        state.speechActive = true;
        micBtn.classList.add('listening');
      } catch (e) {}
    });

    recognizer.onresult = (event) => {
      state.speechActive = false;
      micBtn.classList.remove('listening');
      const transcript = event.results[0][0].transcript;
      if (transcript) {
        handleUserQuery(transcript);
      }
    };

    recognizer.onerror = () => {
      state.speechActive = false;
      micBtn.classList.remove('listening');
    };

    recognizer.onend = () => {
      state.speechActive = false;
      micBtn.classList.remove('listening');
    };
  }
}

function speakText(text) {
  if (!('speechSynthesis' in window)) return;
  window.speechSynthesis.cancel();

  const utterance = new SpeechSynthesisUtterance(text);
  const langMap = { en: 'en-IN', hi: 'hi-IN', te: 'te-IN', ta: 'ta-IN' };
  utterance.lang = langMap[state.lang] || 'en-IN';
  utterance.rate = 1.05;
  utterance.pitch = 1.0;
  window.speechSynthesis.speak(utterance);
}

function cleanTextForTTS(htmlText) {
  return htmlText.replace(/<[^>]*>/g, '').replace(/[*_#]/g, '');
}

function setText(id, text) {
  const el = document.getElementById(id);
  if (el && el.textContent !== text) {
    el.textContent = text;
  }
}
