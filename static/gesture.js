/* Lean the fleeing drone: the laptop camera watches shoulders and nose. */
const Gesture = (() => {
  const ON = 0.2;
  const OFF = 0.11;
  const NOSE = 0;
  const L_SHOULDER = 11;
  const R_SHOULDER = 12;

  let shown = false;
  let live = false;
  let running = false;
  let starting = false;
  let generation = 0;
  let stream = null;
  let landmarker = null;
  let loading = null;
  let base = null;
  let samples = [];
  let prev = { right: 0, forward: 0 };
  let lastSent = { right: 0, forward: 0 };
  let lastSentAt = 0;
  let lastDetect = 0;
  let stamp = 0;

  const video = () => document.getElementById("gesture-video");
  const statusEl = () => document.getElementById("gesture-status");
  const camButton = () => document.getElementById("gesture-cam");

  function axis(value, previous) {
    if (value > (previous === 1 ? OFF : ON)) return 1;
    if (value < (previous === -1 ? -OFF : -ON)) return -1;
    return 0;
  }

  function average(key) {
    return samples.reduce((sum, sample) => sum + sample[key], 0) / samples.length;
  }

  function bodyOf(landmarks) {
    if (!landmarks || landmarks.length < 13) return null;
    const nose = landmarks[NOSE];
    const left = landmarks[L_SHOULDER];
    const right = landmarks[R_SHOULDER];
    const visible = (point) => point && (point.visibility == null || point.visibility >= 0.5);
    if (![nose, left, right].every(visible)) return null;
    const shoulderW = Math.hypot(right.x - left.x, right.y - left.y);
    if (shoulderW < 0.05) return null;
    const sx = (left.x + right.x) / 2;
    const sy = (left.y + right.y) / 2;
    return {
      // The camera faces the player, so their right is the left side of the image.
      rightAmount: (sx - nose.x) / shoulderW,
      headUp: (sy - nose.y) / shoulderW,
      shoulderW,
    };
  }

  function classify(sample) {
    if (!sample) {
      prev = { right: 0, forward: 0 };
      return { right: 0, forward: 0, seen: false, ready: !!base };
    }
    if (!base) {
      samples.push(sample);
      if (samples.length >= 12) {
        const spread = (key) => Math.max(...samples.map((s) => s[key])) - Math.min(...samples.map((s) => s[key]));
        if (spread("rightAmount") > 0.12 || spread("headUp") > 0.12) samples = samples.slice(-4);
        else {
          base = { rightAmount: average("rightAmount"), headUp: average("headUp"), shoulderW: average("shoulderW") };
          samples = [];
        }
      }
      return { right: 0, forward: 0, seen: true, ready: false };
    }
    const dRight = sample.rightAmount - base.rightAmount;
    const dForward = (base.headUp - sample.headUp) * 0.7 + (sample.shoulderW / base.shoulderW - 1);
    const right = axis(dRight, prev.right);
    const forward = axis(dForward, prev.forward);
    prev = { right, forward };
    return { right, forward, seen: true, ready: true };
  }

  function leanText(right, forward) {
    const way = (forward > 0 ? "前" : forward < 0 ? "後ろ" : "") + (right > 0 ? "右" : right < 0 ? "左" : "");
    return way ? way + "に傾いています" : "まっすぐです";
  }

  function showStatus(lean) {
    const el = statusEl();
    if (!el) return;
    let text;
    if (!lean.seen) text = "顔と肩が映るようにしてください";
    else if (!lean.ready) text = "まっすぐな姿勢で、少し止まってください";
    else text = leanText(lean.right, lean.forward);
    if (live && lean.ready && lean.seen) {
      text += lean.right || lean.forward ? "。この方向へ一歩ずつ動きます" : "。止まっています";
    } else if (!live && lean.ready) {
      text += "。離陸すると、この傾きで動きます";
    }
    el.textContent = text;
  }

  async function send(lean) {
    if (!live || typeof post !== "function") return;
    const now = performance.now();
    const changed = lean.right !== lastSent.right || lean.forward !== lastSent.forward;
    if (!changed && now - lastSentAt < 250) return;
    lastSent = { right: lean.right, forward: lean.forward };
    lastSentAt = now;
    try { await post("/api/gesture", { right: lean.right, forward: lean.forward }); } catch (e) { /* next tick retries */ }
  }

  function nowStamp() {
    const t = performance.now();
    stamp = t > stamp ? t : stamp + 1;
    return stamp;
  }

  function tick() {
    if (!running) return;
    const cam = video();
    const t = performance.now();
    if (landmarker && cam && cam.readyState >= 2 && t - lastDetect >= 100) {
      lastDetect = t;
      let landmarks = null;
      try {
        const found = landmarker.detectForVideo(cam, nowStamp());
        landmarks = found.landmarks && found.landmarks[0];
      } catch (e) { /* a dropped frame is just a miss */ }
      const lean = classify(bodyOf(landmarks));
      showStatus(lean);
      send(lean);
    }
    if (running && cam && cam.requestVideoFrameCallback) cam.requestVideoFrameCallback(tick);
    else if (running) setTimeout(tick, 120);
  }

  async function ensureModel() {
    if (landmarker) return;
    if (!loading) {
      loading = (async () => {
        const vision = await import("/static/vendor/pose/vision_bundle.mjs");
        const files = await vision.FilesetResolver.forVisionTasks("/static/vendor/pose/wasm");
        landmarker = await vision.PoseLandmarker.createFromOptions(files, {
          baseOptions: {
            modelAssetPath: "/static/vendor/pose/pose_landmarker_lite.task",
            delegate: "CPU",
          },
          runningMode: "VIDEO",
          numPoses: 1,
        });
      })();
    }
    try {
      await loading;
    } catch (e) {
      loading = null;
      throw e;
    }
  }

  function stop() {
    generation++;
    running = false;
    starting = false;
    stream && stream.getTracks().forEach((track) => track.stop());
    stream = null;
    const cam = video();
    if (cam) cam.srcObject = null;
    const view = document.getElementById("gesture-view");
    if (view) view.hidden = true;
    const button = camButton();
    if (button) {
      button.disabled = false;
      button.textContent = "カメラをオン";
    }
    base = null;
    samples = [];
    prev = { right: 0, forward: 0 };
    if (live && (lastSent.right || lastSent.forward) && typeof post === "function") {
      lastSent = { right: 0, forward: 0 };
      post("/api/gesture", { right: 0, forward: 0 });
    }
  }

  async function start() {
    if (running || starting) return;
    const mine = ++generation;
    starting = true;
    const button = camButton();
    const view = document.getElementById("gesture-view");
    const el = statusEl();
    if (button) button.disabled = true;
    if (view) view.hidden = false;
    if (el) el.textContent = "カメラと体の認識を準備しています…";
    try {
      await ensureModel();
      stream = await navigator.mediaDevices.getUserMedia({
        audio: false,
        video: { facingMode: "user", width: { ideal: 640 }, height: { ideal: 480 } },
      });
    } catch (e) {
      starting = false;
      if (button) button.disabled = false;
      if (view) view.hidden = true;
      if (el) {
        el.textContent = e && e.name === "NotAllowedError"
          ? "カメラが許可されていません。ブラウザの許可を確認してください"
          : (landmarker ? "カメラを使えませんでした" : "体の認識を読み込めませんでした");
      }
      stream && stream.getTracks().forEach((track) => track.stop());
      stream = null;
      return;
    }
    if (mine !== generation || !shown) {
      stream.getTracks().forEach((track) => track.stop());
      stream = null;
      starting = false;
      return;
    }
    const cam = video();
    cam.srcObject = stream;
    const played = cam.play();
    if (played && played.catch) played.catch(() => {});
    base = null;
    samples = [];
    prev = { right: 0, forward: 0 };
    lastDetect = 0;
    running = true;
    starting = false;
    if (button) {
      button.disabled = false;
      button.textContent = "カメラをオフ";
    }
    if (el) el.textContent = "顔と肩が映るようにしてください";
    if (cam.requestVideoFrameCallback) cam.requestVideoFrameCallback(tick);
    else setTimeout(tick, 120);
  }

  function setVisible(show, isLive) {
    shown = !!show;
    live = !!isLive && shown;
    const card = document.getElementById("gesture-card");
    if (!card) return;
    card.hidden = !shown;
    if (!shown) stop();
  }

  const button = camButton();
  if (button) {
    button.addEventListener("click", () => (running || starting ? stop() : start()));
  }
  window.addEventListener("pagehide", stop);

  return { setVisible, stop };
})();
window.Gesture = Gesture;
