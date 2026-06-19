/* Speech-to-text mic button for the Chainlit composer.
 *
 * Flow: click mic -> record (live waveform) -> stop -> POST audio to
 * /transcribe -> drop the returned text into the input box. The user then
 * edits if needed and hits send. No API key ever touches the browser.
 */
(function () {
  "use strict";

  const LOG = "[STT]";
  const ENDPOINT = "/transcribe";

  let mediaRecorder = null;
  let chunks = [];
  let stream = null;
  let audioCtx = null;
  let analyser = null;
  let rafId = null;
  let recording = false;
  let cancelled = false;

  // ---- helpers ------------------------------------------------------------

  function findInput() {
    return document.querySelector("#chat-input") || document.querySelector("textarea");
  }

  function findSubmit() {
    return (
      document.querySelector("#chat-submit") ||
      document.querySelector('button[type="submit"]')
    );
  }

  // React controls the textarea value, so a plain `el.value = x` is ignored.
  // We use the native setter and dispatch a real input event so React updates.
  function setInputValue(el, value) {
    const proto = window.HTMLTextAreaElement.prototype;
    const setter = Object.getOwnPropertyDescriptor(proto, "value").set;
    setter.call(el, value);
    el.dispatchEvent(new Event("input", { bubbles: true }));
  }

  function insertTranscript(text) {
    const input = findInput();
    if (!input) return;
    const existing = input.value || "";
    const combined = existing.trim() ? existing.trimEnd() + " " + text : text;
    setInputValue(input, combined);
    input.focus();
  }

  function pickMimeType() {
    const candidates = [
      "audio/webm;codecs=opus",
      "audio/webm",
      "audio/ogg;codecs=opus",
      "audio/mp4",
    ];
    if (window.MediaRecorder && MediaRecorder.isTypeSupported) {
      for (const t of candidates) {
        if (MediaRecorder.isTypeSupported(t)) return t;
      }
    }
    return "";
  }

  // ---- UI: mic button -----------------------------------------------------

  function micIconSVG() {
    return (
      '<svg viewBox="0 0 24 24" width="20" height="20" fill="none" ' +
      'stroke="currentColor" stroke-width="2" stroke-linecap="round" ' +
      'stroke-linejoin="round">' +
      '<path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z"></path>' +
      '<path d="M19 10v2a7 7 0 0 1-14 0v-2"></path>' +
      '<line x1="12" y1="19" x2="12" y2="23"></line>' +
      '<line x1="8" y1="23" x2="16" y2="23"></line>' +
      "</svg>"
    );
  }

  function spinnerSVG() {
    return '<span class="stt-spinner" aria-hidden="true"></span>';
  }

  function setButtonState(state) {
    const btn = document.getElementById("stt-mic-btn");
    if (!btn) return;
    btn.classList.remove("stt-recording", "stt-loading");
    if (state === "recording") {
      btn.classList.add("stt-recording");
      btn.innerHTML = micIconSVG();
      btn.title = "Stop recording";
    } else if (state === "loading") {
      btn.classList.add("stt-loading");
      btn.innerHTML = spinnerSVG();
      btn.title = "Transcribing…";
    } else {
      btn.innerHTML = micIconSVG();
      btn.title = "Speak your question";
    }
  }

  function ensureButton() {
    if (document.getElementById("stt-mic-btn")) return;
    const submit = findSubmit();
    const input = findInput();
    if (!input) return; // composer not mounted yet

    const btn = document.createElement("button");
    btn.id = "stt-mic-btn";
    btn.type = "button";
    btn.className = "stt-mic-btn";
    btn.title = "Speak your question";
    btn.innerHTML = micIconSVG();
    btn.addEventListener("click", function (e) {
      e.preventDefault();
      e.stopPropagation();
      toggle();
    });

    if (submit && submit.parentElement) {
      submit.parentElement.insertBefore(btn, submit);
    } else {
      // Fallback: float it inside the composer.
      btn.classList.add("stt-mic-floating");
      (input.parentElement || document.body).appendChild(btn);
    }
  }

  // ---- UI: recording bar with waveform ------------------------------------

  function showRecordingBar() {
    hideRecordingBar();
    const bar = document.createElement("div");
    bar.id = "stt-rec-bar";
    bar.className = "stt-rec-bar";
    bar.innerHTML =
      '<button type="button" class="stt-rec-cancel" title="Cancel">✕</button>' +
      '<canvas class="stt-wave" width="320" height="40"></canvas>' +
      '<span class="stt-timer">0:00</span>' +
      '<button type="button" class="stt-rec-stop" title="Stop & transcribe">' +
      "Stop</button>";
    document.body.appendChild(bar);

    bar.querySelector(".stt-rec-cancel").addEventListener("click", cancelRecording);
    bar.querySelector(".stt-rec-stop").addEventListener("click", stopRecording);

    positionRecordingBar();
    startTimer();
    drawWaveform();
  }

  function positionRecordingBar() {
    const bar = document.getElementById("stt-rec-bar");
    const input = findInput();
    if (!bar || !input) return;
    const r = input.getBoundingClientRect();
    bar.style.left = r.left + "px";
    bar.style.width = r.width + "px";
    bar.style.bottom = window.innerHeight - r.top + 8 + "px";
  }

  function hideRecordingBar() {
    const bar = document.getElementById("stt-rec-bar");
    if (bar) bar.remove();
  }

  let timerStart = 0;
  let timerId = null;
  function startTimer() {
    timerStart = Date.now();
    timerId = setInterval(function () {
      const el = document.querySelector("#stt-rec-bar .stt-timer");
      if (!el) return;
      const s = Math.floor((Date.now() - timerStart) / 1000);
      el.textContent = Math.floor(s / 60) + ":" + String(s % 60).padStart(2, "0");
    }, 250);
  }
  function stopTimer() {
    if (timerId) clearInterval(timerId);
    timerId = null;
  }

  function drawWaveform() {
    const canvas = document.querySelector("#stt-rec-bar .stt-wave");
    if (!canvas || !analyser) return;
    const ctx = canvas.getContext("2d");
    const buf = new Uint8Array(analyser.fftSize);

    function frame() {
      rafId = requestAnimationFrame(frame);
      analyser.getByteTimeDomainData(buf);
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      ctx.lineWidth = 2;
      const accent =
        getComputedStyle(document.documentElement).getPropertyValue("--stt-accent") ||
        "#ef4444";
      ctx.strokeStyle = accent.trim() || "#ef4444";
      ctx.beginPath();
      const slice = canvas.width / buf.length;
      let x = 0;
      for (let i = 0; i < buf.length; i++) {
        const v = buf[i] / 128.0; // 0..2, 1 = silence
        const y = (v * canvas.height) / 2;
        if (i === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
        x += slice;
      }
      ctx.stroke();
    }
    frame();
  }

  // ---- recording lifecycle ------------------------------------------------

  function toggle() {
    if (recording) stopRecording();
    else startRecording();
  }

  async function startRecording() {
    if (recording) return;
    cancelled = false;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch (err) {
      console.error(LOG, "mic permission denied", err);
      alert("Microphone access is blocked. Please allow it in your browser.");
      return;
    }

    const mimeType = pickMimeType();
    chunks = [];
    try {
      mediaRecorder = mimeType
        ? new MediaRecorder(stream, { mimeType })
        : new MediaRecorder(stream);
    } catch (err) {
      console.error(LOG, "MediaRecorder init failed", err);
      cleanupStream();
      return;
    }

    mediaRecorder.ondataavailable = function (e) {
      if (e.data && e.data.size > 0) chunks.push(e.data);
    };
    mediaRecorder.onstop = onRecorderStop;

    // Set up the live waveform analyser.
    try {
      audioCtx = new (window.AudioContext || window.webkitAudioContext)();
      analyser = audioCtx.createAnalyser();
      analyser.fftSize = 1024;
      audioCtx.createMediaStreamSource(stream).connect(analyser);
    } catch (err) {
      console.warn(LOG, "waveform unavailable", err);
    }

    mediaRecorder.start();
    recording = true;
    setButtonState("recording");
    showRecordingBar();
    window.addEventListener("resize", positionRecordingBar);
  }

  function stopRecording() {
    if (!recording || !mediaRecorder) return;
    recording = false;
    try {
      mediaRecorder.stop();
    } catch (err) {
      console.error(LOG, "stop failed", err);
    }
  }

  function cancelRecording() {
    cancelled = true;
    stopRecording();
  }

  function onRecorderStop() {
    teardownWaveform();
    hideRecordingBar();
    stopTimer();
    window.removeEventListener("resize", positionRecordingBar);

    const mimeType = (mediaRecorder && mediaRecorder.mimeType) || "audio/webm";
    cleanupStream();

    if (cancelled) {
      setButtonState("idle");
      chunks = [];
      return;
    }

    const blob = new Blob(chunks, { type: mimeType });
    chunks = [];
    if (blob.size === 0) {
      setButtonState("idle");
      return;
    }
    sendForTranscription(blob, mimeType);
  }

  async function sendForTranscription(blob, mimeType) {
    setButtonState("loading");
    try {
      const resp = await fetch(ENDPOINT, {
        method: "POST",
        headers: { "Content-Type": mimeType },
        body: blob,
      });
      const data = await resp.json().catch(function () {
        return {};
      });
      if (!resp.ok) {
        throw new Error(data.error || "Transcription failed (" + resp.status + ")");
      }
      const text = (data.text || "").trim();
      if (text) insertTranscript(text);
      else console.warn(LOG, "empty transcript");
    } catch (err) {
      console.error(LOG, err);
      alert(err.message || "Could not transcribe audio. Please try again.");
    } finally {
      setButtonState("idle");
    }
  }

  // ---- cleanup ------------------------------------------------------------

  function teardownWaveform() {
    if (rafId) cancelAnimationFrame(rafId);
    rafId = null;
    if (audioCtx) {
      audioCtx.close().catch(function () {});
      audioCtx = null;
    }
    analyser = null;
  }

  function cleanupStream() {
    if (stream) {
      stream.getTracks().forEach(function (t) {
        t.stop();
      });
      stream = null;
    }
  }

  // ---- bootstrap ----------------------------------------------------------

  // Chainlit mounts/re-renders the composer; keep the button present.
  const observer = new MutationObserver(function () {
    ensureButton();
  });
  observer.observe(document.body, { childList: true, subtree: true });
  ensureButton();
  console.log(LOG, "mic button ready");
})();
