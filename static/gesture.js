/* Move the drone with one hand: the laptop camera watches the wrist. */
const Gesture = (() => {
  const ON = 1.0;   // palm-widths from where the hand was held still
  const OFF = 0.55;
  const WRIST = 0;
  const MIDDLE = 9;

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

  function handOf(landmarks) {
    if (!landmarks || landmarks.length < 10) return null;
    const wrist = landmarks[WRIST];
    const middle = landmarks[MIDDLE];
    if (!wrist || !middle) return null;
    const size = Math.hypot(middle.x - wrist.x, middle.y - wrist.y);
    if (size < 0.02) return null;
    return { x: wrist.x, y: wrist.y, size };
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
        if (spread("x") > 0.03 || spread("y") > 0.03) samples = samples.slice(-4);
        else {
          base = { x: average("x"), y: average("y"), size: average("size") };
          samples = [];
        }
      }
      return { right: 0, forward: 0, seen: true, ready: false };
    }
    // The camera faces the player, so their right is the left side of the image.
    // A hand raised in the picture is "forward".
    const dRight = (base.x - sample.x) / base.size;
    const dForward = (base.y - sample.y) / base.size;
    const right = axis(dRight, prev.right);
    const forward = axis(dForward, prev.forward);
    prev = { right, forward };
    return { right, forward, seen: true, ready: true };
  }

  function leanText(right, forward) {
    const way = (forward > 0 ? "上" : forward < 0 ? "下" : "") + (right > 0 ? "右" : right < 0 ? "左" : "");
    return way ? "手を" + way + "に出しています" : "手が止まっています";
  }

  function showStatus(lean) {
    const el = statusEl();
    if (!el) return;
    let text;
    if (!lean.seen) text = "片手をカメラに映してください";
    else if (!lean.ready) text = "手を前に出して、少し止まってください";
    else text = leanText(lean.right, lean.forward);
    if (live && lean.ready && lean.seen) {
      text += lean.right || lean.forward ? "。この方向へ一歩ずつ動きます" : "。止まっています";
    } else if (!live && lean.ready) {
      text += "。離陸すると、この手の位置で動きます";
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
      const lean = classify(handOf(landmarks));
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
        landmarker = await vision.HandLandmarker.createFromOptions(files, {
          baseOptions: {
            modelAssetPath: "/static/vendor/pose/hand_landmarker.task",
            delegate: "CPU",
          },
          runningMode: "VIDEO",
          numHands: 1,
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
    if (el) el.textContent = "カメラと手の認識を準備しています…";
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
          : (landmarker ? "カメラを使えませんでした" : "手の認識を読み込めませんでした");
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
    if (el) el.textContent = "片手をカメラに映してください";
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
