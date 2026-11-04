const $ = (id) => document.getElementById(id);
const page = document.body.dataset.page;
let editing = false;

async function post(url, body) {
  const res = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" },
                                 body: JSON.stringify(body || {}) });
  return res.json();
}

function batteryText(battery) {
  if (!battery) return "—";
  return battery + "%" + (Number(battery) < 30 ? "（離陸不可）" : "");
}

function droneLabel(d) { return (d.name || "名前なし") + "（" + (d.ssid || d.ip) + "）"; }

async function copy(text, button) {
  try { await navigator.clipboard.writeText(text); } catch (e) { return; }
  const old = button.textContent;
  button.textContent = "コピーしました";
  setTimeout(() => button.textContent = old, 1200);
}

function renderHeader(s) {
  $("local-ip").textContent = s.local_ip || "ネットワークなし";
  $("mode").textContent = { router: "ルーターにつながっています", direct: "TelloのWi-Fiに直接つながっています",
                            offline: "Wi-Fiにつながっていません" }[s.mode];
  $("mode").className = "badge " + s.mode;
}

function renderDrones(s) {
  // On a Tello's own Wi-Fi, offer the router setup right here.
  $("join-card").hidden = !(s.mode === "direct" && s.drones.length);
  $("scan").disabled = s.scanning;
  $("scan-msg").textContent = s.message + (s.scanned_at ? "（" + s.scanned_at + "）" : "");
  if (editing) return;
  $("drones").innerHTML = s.drones.length ? "" :
    '<tr><td colspan="5" class="muted">' +
    (s.scanned_at ? "見つかりませんでした。電源・Wi-Fiを確認してください" : "まだ探していません") + "</td></tr>";
  for (const d of s.drones) {
    const tr = document.createElement("tr");
    tr.innerHTML = '<td><input maxlength="20" placeholder="例: ゆーしん機"><div class="saved muted"></div></td>' +
      '<td class="mono"></td><td></td>' +
      '<td><button class="ghost claim"></button></td><td><button class="ghost claim-b"></button></td>';
    const input = tr.querySelector("input");
    const saved = tr.querySelector(".saved");
    input.value = d.name;
    const save = async () => {
      await post("/api/name", { sn: d.sn, name: input.value.trim() });
      saved.textContent = input.value.trim() ? "保存しました ✓" : "名前を消しました";
      editing = false;
      setTimeout(refresh, 1200);
    };
    input.addEventListener("focus", () => { editing = true; saved.textContent = ""; });
    input.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); input.blur(); } });
    // Saved on Enter or when leaving the box, so a typed name is never lost.
    input.addEventListener("blur", () => {
      if (input.value.trim() !== d.name) save(); else editing = false;
    });
    tr.children[1].textContent = d.ssid || "?";
    tr.children[1].title = "IP " + d.ip;
    tr.children[2].textContent = batteryText(d.battery);
    tr.children[2].className = d.battery && Number(d.battery) < 30 ? "error" : "";
    const mine = s.my_drone && s.my_drone.sn === d.sn;
    const claim = tr.querySelector(".claim");
    claim.textContent = mine ? "自分の機体 ✓" : "自分の機体にする";
    claim.className = mine ? "claim" : "ghost claim";
    claim.addEventListener("click", async () => {
      claim.textContent = "つないでいます…";
      claim.disabled = true;
      editing = true;  // keep this row (and its message) while waiting
      const r = await post("/api/claim", { sn: d.sn });
      saved.className = r.ok ? "saved muted" : "saved error";
      saved.textContent = r.ok ? "つなぎました ✓" : r.message;
      setTimeout(() => { editing = false; refresh(); }, r.ok ? 800 : 6000);
    });
    const isB = s.my_drone_b && s.my_drone_b.sn === d.sn;
    const claimB = tr.querySelector(".claim-b");
    claimB.textContent = isB ? "B機 ✓" : "B機にする";
    claimB.className = isB ? "claim-b" : "ghost claim-b";
    claimB.title = "1台のPCで2台を動かすときの、2台目の機体";
    claimB.addEventListener("click", async () => {
      claimB.textContent = "つないでいます…";
      claimB.disabled = true;
      editing = true;
      const r = await post("/api/claim", { sn: d.sn, slot: "B" });
      saved.className = r.ok ? "saved muted" : "saved error";
      saved.textContent = r.ok ? "B機につなぎました ✓" : r.message;
      setTimeout(() => { editing = false; refresh(); }, r.ok ? 800 : 6000);
    });
    $("drones").appendChild(tr);
  }
}

let meLoaded = false;
let localMode = false;  // the matching form: false = two PCs, true = one PC flying both drones

function showLocalMode(on) {
  localMode = on;
  for (const b of document.querySelectorAll(".segment [data-local]")) b.classList.toggle("on", (b.dataset.local === "1") === on);
  $("drone-b-label").hidden = !on;
  $("code-label").hidden = on;
  $("my-drone-label").textContent = on ? "A機のドローン" : "自分のドローン";
  $("match-help").textContent = on
    ? "1台のPCでA機・B機の2台を動かします。合言葉も相手のPCも要りません。"
    : "相手と同じ4桁の合言葉を入れて「マッチング開始」を押します。";
}

function droneText(d) {
  if (!d) return "ドローン未選択";
  return (d.name || "名前なし") + "（" + (d.ip ? (d.ssid || d.ip) : "未確認") + "）";
}

function renderPeers(s) {
  if (!meLoaded) {
    $("player").value = s.player; $("code").value = s.code; meLoaded = true;
    showLocalMode(s.local);
  }
  if (s.matching && s.local !== localMode) showLocalMode(s.local);
  // Drones from the last scan, plus the saved one even if it is not found right now.
  const options = [["", "選んでください"]].concat(s.drones.map((d) => [d.sn, droneLabel(d)]));
  if (s.my_drone && !s.drones.some((d) => d.sn === s.my_drone.sn)) {
    options.push([s.my_drone.sn, (s.my_drone.name || s.my_drone.sn) + "（今は見つかっていません）"]);
  }
  if (fillSelect($("my-drone"), options) && s.my_drone) $("my-drone").value = s.my_drone.sn;
  const optionsB = [["", "選んでください"]].concat(s.drones.map((d) => [d.sn, droneLabel(d)]));
  if (s.my_drone_b && !s.drones.some((d) => d.sn === s.my_drone_b.sn)) {
    optionsB.push([s.my_drone_b.sn, (s.my_drone_b.name || s.my_drone_b.sn) + "（今は見つかっていません）"]);
  }
  if (fillSelect($("my-drone-b"), optionsB) && s.my_drone_b) $("my-drone-b").value = s.my_drone_b.sn;

  // Inputs are locked while matching.
  for (const id of ["player", "my-drone", "my-drone-b", "code"]) $(id).disabled = s.matching;
  for (const b of document.querySelectorAll(".segment [data-local]")) b.disabled = s.matching;
  $("match").hidden = s.matching;
  $("unmatch").hidden = !s.matching;
  $("status").hidden = !s.matching;

  const opponent = s.matching ? s.peers[0] : null;
  $("waiting").hidden = !s.matching || !!opponent;
  $("matched").hidden = !opponent;
  $("waiting-detail").textContent = "合言葉「" + s.code + "」で探しています。相手にも同じ合言葉でマッチングを始めてもらってください";
  if (opponent) {
    $("me-name").textContent = s.local ? "A機" : s.player;
    $("me-drone").textContent = droneText(s.my_drone);
    $("op-name").textContent = opponent.player || "名前なし";
    $("op-drone").textContent = droneText(opponent.drone);
    const others = s.peers.slice(1).map((p) => p.player || "名前なし");
    $("others").textContent = others.length ? "同じ合言葉の人がほかにもいます: " + others.join("、") : "";
  }
  $("next").classList.toggle("disabled", !opponent);
  $("next").setAttribute("aria-disabled", String(!opponent));
}

function fillSelect(select, options) {
  const keep = select.value;
  const sig = JSON.stringify(options);
  if (select.dataset.sig === sig) return false;
  select.dataset.sig = sig;
  select.innerHTML = "";
  for (const [value, label] of options) select.add(new Option(label, value));
  if (options.some(([v]) => v === keep)) select.value = keep;
  return true;
}

// ---- battle room ----
const STATE_TEXT = { idle: "待機中", starting: "離陸中…", flying: "飛行中", down: "地面に落ちた", landed: "着陸" };

let side = "A";  // 1-PC mode: whose plan the picker is showing ("A" or "B")
const planKey = (s) => (s.local && side === "B" ? s.plan_key_b : s.plan_key);

function setPlayerCard(prefix, name, drone, ready) {
  $(prefix + "-name").textContent = name || "名前なし";
  $(prefix + "-drone").textContent = drone ? (drone.name || "名前なし") : "ドローン未選択";
  $(prefix + "-ssid").textContent = drone && drone.ssid ? drone.ssid : "Wi-Fi名 未確認";
  $(prefix + "-battery").textContent = "電池 " + batteryText(drone && drone.battery);
  $(prefix + "-battery").className = drone && drone.battery && Number(drone.battery) < 30 ? "error" : "muted";
  $(prefix + "-ready").textContent = ready ? "準備OK" : "準備中…";
  $(prefix + "-ready").className = "ready-badge" + (ready ? " on" : "");
}

function choiceCard(c, s, onPick) {
  const card = document.createElement("div");
  card.className = "mode-card" + (c.key === planKey(s) ? " on" : "") + (c.imported ? " imported" : "");
  card.tabIndex = 0;
  card.innerHTML = "<b></b><span></span>";
  card.children[0].textContent = c.title;
  card.children[1].textContent = c.description;
  card.addEventListener("click", onPick);
  card.addEventListener("keydown", (e) => { if (e.key === "Enter") onPick(); });
  return card;
}

let view = null;  // "modes" or "import": which list the top-right switch shows

function showView(name) {
  view = name;
  for (const b of document.querySelectorAll(".segment button[data-view]")) {
    b.classList.toggle("on", b.dataset.view === name);
    b.setAttribute("aria-selected", String(b.dataset.view === name));
  }
  $("mode-list").hidden = name !== "modes";
  $("import-view").hidden = name !== "import";
}

function renderModes(s) {
  if (view === null) {
    // First time: open the side that holds the current choice.
    const chosen = s.choices.find((c) => c.key === planKey(s));
    showView(chosen && chosen.imported ? "import" : "modes");
  }

  const sig = JSON.stringify([s.choices, planKey(s), s.ready, side]);
  if ($("mode-list").dataset.sig === sig) return;
  $("mode-list").dataset.sig = sig;

  // 4つのタイプ
  const modes = $("mode-list");
  modes.innerHTML = "";
  for (const c of s.choices.filter((c) => !c.imported)) {
    modes.appendChild(choiceCard(c, s, () => choosePlan(...splitPlan(c.key))));
  }

  // ファイルインポート: imported files, then the button to add one
  const files = $("plan-list");
  files.innerHTML = "";
  for (const c of s.choices.filter((c) => c.imported)) {
    const card = choiceCard(c, s, () => choosePlan(...splitPlan(c.key)));
    const remove = document.createElement("button");
    remove.className = "remove";
    remove.title = "このファイルを外す";
    remove.textContent = "×";
    remove.addEventListener("click", async (e) => {
      e.stopPropagation();
      if (!confirm("「" + c.title + "」を外しますか？")) return;
      await post("/api/plans/delete", { name: splitPlan(c.key)[1] });
      $("mode-list").dataset.sig = "";
      refresh();
    });
    card.appendChild(remove);
    files.appendChild(card);
  }
  const add = document.createElement("button");
  add.className = "mode-card import-card";
  add.innerHTML = "<b>＋ ファイルを読み込む</b><span>PCの .txt を選ぶ</span>";
  add.addEventListener("click", () => $("import-file").click());
  files.appendChild(add);
}

async function importFile(file) {
  const result = $("import-result");
  result.innerHTML = "";
  const text = await file.text();
  const r = await post("/api/import", { filename: file.name, text });
  const head = document.createElement("p");
  head.className = r.ok ? "ok-text" : "error";
  head.textContent = r.ok
    ? "「" + file.name + "」を読み込みました ✓（命令" + r.commands + "個・一番遠くて" + r.farthest + "cm）"
    : "「" + file.name + "」は使えません:";
  result.appendChild(head);
  for (const e of r.errors || []) {
    const p = document.createElement("p");
    p.className = "error";
    p.textContent = (e.line ? e.line + "行目「" + e.text.trim() + "」: " : "") + e.message;
    result.appendChild(p);
  }
  $("mode-list").dataset.sig = "";
  refresh();
}

async function choosePlan(kind, name) {
  const r = await post("/api/plan", { kind, name, side });
  if (!r.ok) $("room-status").textContent = r.message;
  refresh();
}

// Choices are "mode:<id>" (built in) or "plan:<file>" (imported).
function splitPlan(value) {
  const i = value.indexOf(":");
  return [value.slice(0, i), value.slice(i + 1)];
}

function droneState(b) {
  if (!b) return "?";
  return (STATE_TEXT[b.phase] || b.phase) + (b.airborne && b.height != null ? "（高さ " + b.height + "cm）" : "");
}

// ---- the pad arena map (screen right = the rockets' direction, A to B) ----
// pads: {id: [x, y]}; drones: [{pos: {x, y, heading, pad}, cls, label}]
function drawMap(el, field, pads, starts, drones) {
  const [xMin, xMax, yMin, yMax] = field;
  const scale = Math.min(1.6, 500 / Math.max(xMax - xMin, yMax - yMin));
  const W = (xMax - xMin) * scale, H = (yMax - yMin) * scale;
  const sx = (x) => (x - xMin) * scale, sy = (y) => (y - yMin) * scale;
  const pad = 19.5 * scale;
  let svg = `<svg viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" role="img" aria-label="フィールドの地図">`;
  svg += `<rect x="0" y="0" width="${W}" height="${H}" class="field"/>`;
  for (const [id, xy] of Object.entries(pads)) {
    if (!xy) continue;
    const [x, y] = xy;
    svg += `<rect x="${sx(x) - pad / 2}" y="${sy(y) - pad / 2}" width="${pad}" height="${pad}" rx="4" class="pad"/>`;
    svg += `<text x="${sx(x)}" y="${sy(y) + 5}" class="pad-id">${id}</text>`;
    svg += `<text x="${sx(x) + pad / 2 + 8}" y="${sy(y) + 4}" class="rocket">▶</text>`;
  }
  for (const [side, [x, y]] of Object.entries(starts || {})) {
    svg += `<text x="${sx(x)}" y="${sy(y) + (side === "A" ? 24 : -14)}" class="side">${side}側</text>`;
  }
  for (const d of drones) {
    if (!d.pos) continue;
    const rad = (d.pos.heading || 0) * Math.PI / 180;
    const hx = sx(d.pos.x + Math.cos(rad) * 18), hy = sy(d.pos.y + Math.sin(rad) * 18);
    svg += `<line x1="${sx(d.pos.x)}" y1="${sy(d.pos.y)}" x2="${hx}" y2="${hy}" class="${d.cls} heading"/>` +
      `<circle cx="${sx(d.pos.x)}" cy="${sy(d.pos.y)}" r="9" class="${d.cls}"/>` +
      `<text x="${sx(d.pos.x)}" y="${sy(d.pos.y) - 13}" class="label">${d.label}${d.pos.pad ? "（パッド" + d.pos.pad + "）" : ""}</text>`;
  }
  el.innerHTML = svg + "</svg>";
}

function drawArena(s, opponent) {
  const a = s.arena;
  if (!a) {
    $("arena-map").textContent = "まだ地図がありません。「フィールド」画面でスキャンしてください";
    return;
  }
  const flying = (b) => b && ["starting", "flying"].includes(b.phase);
  drawMap($("arena-map"), a.field, a.pads, a.starts, [
    { pos: flying(opponent && opponent.battle) ? opponent.battle.pos : null, cls: "op", label: s.local ? "B機" : "相手" },
    { pos: flying(s.me) ? s.me.pos : null, cls: "me", label: s.local ? "A機" : "あなた" },
  ]);
}

function arenaSyncText(s, opponent) {
  if (!s.arena) return "フィールドの設定がありません。「フィールド」画面で設定するまで準備OKに進めません。";
  if (!opponent) return "このPCに地図を保存済みです。マッチすると相手と同じ地図にそろえます。";
  return opponent.arena_hash === s.arena_hash ? "両PCの地図は一致しています。"
    : "相手PCと地図をそろえています。一致するまで準備OKに進めません。";
}

// ---- フィールド page: the scan ----
const PHASE_TITLE = { idle: "まだスキャンしていません", running: "スキャン中…", done: "スキャン完了", failed: "スキャン失敗" };

let fieldKind = null;  // which tab the フィールド page shows: "pads" or "simple"
let simpleLoaded = false;

let padCount = null;  // how many pads the scan form is set up for: 4 (square 1 2 / 3 4) or 8

function showPadCount(n) {
  padCount = n;
  for (const b of document.querySelectorAll(".segment [data-pads]")) b.classList.toggle("on", Number(b.dataset.pads) === n);
  for (const ol of document.querySelectorAll("[data-for]")) ol.hidden = Number(ol.dataset.for) !== n;
  document.querySelector(".pad-count").textContent = n;
  document.querySelector(".far-pads").textContent = n === 4 ? "パッド1・2" : "パッド1〜3";
  document.querySelector(".near-note").textContent = n === 4 ? "（パッドはありません。安全な余白）" : "（パッド7・8の側）";
}

// The pad a side takes off from: the pad nearest that side's start on the saved map.
function startPad(arena, role) {
  const [sx, sy] = arena.starts[role];
  return Object.entries(arena.pads).reduce((best, [id, [x, y]]) => {
    const d = Math.hypot(x - sx, y - sy);
    return !best || d < best[1] ? [id, d] : best;
  }, null)[0];
}

function showFieldKind(kind) {
  fieldKind = kind;
  for (const b of document.querySelectorAll(".segment [data-kind]")) b.classList.toggle("on", b.dataset.kind === kind);
  $("pads-card").hidden = kind !== "pads";
  $("simple-card").hidden = kind !== "simple";
  $("pad-badges").hidden = kind !== "pads";
}

function renderField(s) {
  const sc = s.arena_scan;
  const running = sc.phase === "running";
  const savedKind = s.arena ? (s.arena.kind === "simple" ? "simple" : "pads") : null;
  if (fieldKind === null) showFieldKind(running ? "pads" : (savedKind || "pads"));
  if (padCount === null) {
    const saved = s.arena && savedKind === "pads" ? Object.keys(s.arena.pads).length : 0;
    showPadCount(running ? Object.keys(sc.pads).length : (saved === 8 ? 8 : 4));
  }
  const savedPads = s.arena && savedKind === "pads" ? Object.keys(s.arena.pads).length : 0;
  $("kind-note").textContent = !s.arena ? "まだ設定がありません。どちらかで設定するまで準備OKに進めません。" :
    savedKind === "simple" ? `今の設定：パッドなし（2台の距離 ${s.arena.distance}cm）。相手の位置は予想です。` :
    `今の設定：パッドで正確に（スキャンした${savedPads}枚の地図）。`;
  for (const b of document.querySelectorAll(".segment [data-pads]")) b.disabled = running;
  if (s.arena && savedKind === "simple" && !simpleLoaded) {
    $("s-distance").value = s.arena.distance; $("s-back").value = s.arena.back; $("s-side").value = s.arena.side;
    simpleLoaded = true;
  }
  for (const b of document.querySelectorAll(".segment [data-kind]")) b.disabled = running;
  $("simple-save").disabled = running || s.ready;
  if (fieldKind === "simple") {
    $("scan-title").textContent = savedKind === "simple" ? "保存済みの設定（パッドなし）" : "パッドなしの設定はまだありません";
    $("scan-title").className = "";
    $("scan-progress").textContent = "";
    $("scan-sync").textContent = arenaSyncText(s, s.matching ? s.peers[0] : null);
    if (savedKind === "simple") drawMap($("scan-map"), s.arena.field, {}, s.arena.starts, []);
    else $("scan-map").textContent = "下で距離を入れて保存すると、ここに出ます";
    return;
  }
  $("scan-start").hidden = running;
  $("scan-stop").hidden = !running;
  for (const id of ["b-back", "b-front", "b-far", "b-near"]) $(id).disabled = running;
  $("scan-title").textContent = sc.phase === "idle" && s.arena ? "保存済みの地図" : PHASE_TITLE[sc.phase];
  $("scan-title").className = sc.phase === "failed" ? "error" : sc.phase === "done" ? "ok-text" : "";
  $("scan-progress").textContent = sc.phase === "idle" ? "" :
    (running ? `止まって読んだ場所 ${sc.progress[0]} / ${sc.progress[1]} ・ ${sc.elapsed}秒（${sc.time_limit}秒まで）` : sc.message);
  $("scan-log").textContent = sc.log.join("\n");
  $("scan-sync").textContent = arenaSyncText(s, s.matching ? s.peers[0] : null);

  const badges = $("pad-badges");
  badges.innerHTML = "";
  const showScan = sc.phase !== "idle";
  // Which pads to list: the ones being scanned, else the saved map's, else the form's choice.
  const ids = showScan ? Object.keys(sc.pads) : savedPads ? Object.keys(s.arena.pads)
    : Array.from({ length: padCount }, (_, i) => String(i + 1));
  for (const k of ids) {
    const p = showScan ? sc.pads[k] : null;
    const b = document.createElement("span");
    const seen = showScan ? p.seen : (savedPads ? 1 : 0);
    b.className = "pad-badge" + (seen >= 2 || (!showScan && seen) ? " on" : seen ? " some" : "");
    b.textContent = k + (showScan ? "：" + p.seen + "回" + (p.seen ? "・ズレ" + p.spread + "cm" : "") : "");
    badges.appendChild(b);
  }
  if (showScan && sc.field) {
    const pads = {};
    for (const k of ids) pads[k] = sc.pads[k].center;
    drawMap($("scan-map"), sc.field, pads, null,
      [{ pos: running ? { x: sc.pose[0], y: sc.pose[1], heading: 0 } : null, cls: "me", label: "あなた" }]);
  } else if (s.arena) {
    drawMap($("scan-map"), s.arena.field, s.arena.pads, s.arena.starts, []);
  } else {
    $("scan-map").textContent = "まだ地図がありません";
  }
}

function renderBattle(s) {
  const opponent = s.matching ? s.peers[0] : null;
  $("no-match").hidden = !!opponent;
  $("room").hidden = !opponent;
  $("room-part2").hidden = !opponent;
  $("arena-status").hidden = !!s.start_at;
  $("arena-sync").textContent = arenaSyncText(s, opponent);
  if (!opponent) return;
  setPlayerCard("me", s.local ? "A機" : s.player, s.my_drone, s.ready);
  setPlayerCard("op", opponent.player, opponent.drone, opponent.ready);
  const whoLabels = document.querySelectorAll(".room-card .who");
  whoLabels[0].textContent = s.local ? "1台目" : "あなた";
  whoLabels[1].textContent = s.local ? "2台目" : "相手";
  $("side-switch").hidden = !s.local;
  if (!s.local) side = "A";
  for (const b of document.querySelectorAll("#side-switch button")) b.classList.toggle("on", b.dataset.side === side);
  $("start-help").textContent = s.local
    ? "準備OKは1回だけ。押すと5秒後にA機とB機が同時に離陸します。"
    : "2人とも準備OKになると5秒後に同時に離陸します。";
  drawArena(s, opponent);
  const simple = s.arena && s.arena.kind === "simple";
  $("role-line").textContent = s.local
    ? (simple ? `A機とB機を${s.arena.distance}cm離して、向かい合わせに置いてください（パッドなし）。`
      : `A機をパッド${startPad(s.arena, "A")}、B機をパッド${startPad(s.arena, "B")}の中心に、向かい合わせに置いてください。`)
    : "あなたは " + s.role + "側。" + (simple
    ? `相手と${s.arena.distance}cm離して、相手の方を向けて置いてください（パッドなし）。`
    : `地図の開始位置（A＝パッド${startPad(s.arena, "A")}、B＝パッド${startPad(s.arena, "B")}）に相手の方を向けて置いてください。`);
  $("arena-kind-help").textContent = simple
    ? "パッドなしモード：相手の位置は、お互いが送った移動命令から予想します。動くほどずれます。地図の外枠が飛行範囲です。"
    : "パッドモード：地図は「フィールド」画面でドローンが飛んで測ったものです。パッドが見えると位置を直し、見えない間は移動命令から予想します。地図の外枠が飛行範囲です。";
  $("limit").textContent = s.battle_seconds;
  $("log").textContent = (s.local ? ["【A機】", ...s.me.log, "【B機】", ...s.me_b.log] : s.me.log).join("\n");

  const started = !!s.start_at;
  const finished = !!s.result;
  $("setup").hidden = started;
  $("live").hidden = !started || finished;
  $("result").hidden = !finished;

  if (!started) {
    renderModes(s);
    const chosen = s.choices.find((c) => c.key === planKey(s));
    const label = (key) => (s.choices.find((c) => c.key === key) || {}).title;
    const planText = s.local ? "A機: " + (label(s.plan_key) || "未選択") + " ／ B機: " + (label(s.plan_key_b) || "未選択")
      : chosen ? "作戦: " + chosen.title : "作戦を選んでください";
    $("mode-list").classList.toggle("locked", s.ready);
    $("plan-list").classList.toggle("locked", s.ready);
    $("ready").textContent = s.ready ? "準備OKを取り消す" : "準備OK";
    $("ready").className = "big-button" + (s.ready ? " ghost" : "");
    $("ready").disabled = !s.plan || (s.local && !s.plan_b) || (!s.ready && (!s.arena || opponent.arena_hash !== s.arena_hash));
    $("room-status").textContent = planText + " ／ " + (s.ready && opponent.ready ? "まもなくスタート！"
      : s.ready ? "相手の準備を待っています…" : "準備ができたら「準備OK」を押してください");
    return;
  }

  // Countdown, then the live battle.
  const left = s.start_at - s.now;
  $("countdown").hidden = left <= -1.5;
  $("countdown").textContent = left > 0 ? Math.ceil(left) : "スタート！";
  $("fight").hidden = left > 0;
  const elapsed = Math.max(0, s.now - s.start_at);
  $("timer").textContent = "残り " + Math.max(0, Math.ceil(s.battle_seconds - elapsed)) + " 秒";
  const liveLabels = document.querySelectorAll(".live-grid > div > .muted:first-child");
  liveLabels[0].textContent = s.local ? "A機" : "あなた";
  liveLabels[1].textContent = s.local ? "B機" : "相手";
  $("me-state").textContent = droneState(s.me);
  $("me-plan").textContent = s.me.plan ? "作戦: " + s.me.plan : "";
  $("op-state").textContent = droneState(opponent.battle);
  $("op-plan").textContent = opponent.battle && opponent.battle.plan ? "作戦: " + opponent.battle.plan : "";
  // Mode buttons: one row for us, and in the 1-PC mode a second row for the B drone.
  const fillSwitcher = (el, mine, sideName) => {
    const sig = JSON.stringify([s.choices, mine.plan_key, mine.phase]);
    if (el.dataset.sig === sig) return;
    el.dataset.sig = sig;
    el.innerHTML = "";
    if (s.local) {
      const tag = document.createElement("span");
      tag.className = "muted";
      tag.textContent = sideName + "機:";
      el.appendChild(tag);
    }
    for (const c of s.choices) {
      const b = document.createElement("button");
      b.textContent = c.title;
      b.className = c.key === mine.plan_key ? "" : "ghost";
      b.disabled = mine.phase !== "flying";
      b.addEventListener("click", () => post("/api/mode", { key: c.key, side: sideName }).then(refresh));
      el.appendChild(b);
    }
  };
  fillSwitcher($("mode-switch"), s.me, "A");
  $("mode-switch-b").hidden = !s.local;
  if (s.local) fillSwitcher($("mode-switch-b"), s.me_b, "B");
  const up = (b) => ["starting", "flying"].includes(b.phase);
  $("surrender").disabled = !(up(s.me) || (s.local && up(s.me_b)));

  if (finished) {
    const titles = s.local ? { win: "A機の勝ち！", lose: "B機の勝ち！", draw: "引き分け" }
      : { win: "勝ち！", lose: "負け…", draw: "引き分け" };
    $("result-title").textContent = titles[s.result.outcome];
    $("result-title").className = "result-title " + s.result.outcome;
    // The reasons are worded for player A: in the 1-PC mode "相手" is B and "自分" is A.
    $("result-reason").textContent = s.local ? s.result.reason.replaceAll("相手", "B機").replaceAll("自分", "A機") : s.result.reason;
    $("rematch").disabled = up(s.me) || (s.local && up(s.me_b));
  }
}

let started = false;  // no screen updates until a reload's reset has finished

async function refresh() {
  if (!started) return;
  let s;
  try { s = await (await fetch("/api/state")).json(); } catch (e) { return; }
  renderHeader(s);
  if (page === "drones") renderDrones(s);
  if (page === "peers") renderPeers(s);
  if (page === "battle") renderBattle(s);
  if (page === "field") renderField(s);
}

if (page === "drones") {
  $("scan").addEventListener("click", async () => { await post("/api/scan"); refresh(); });
}
if (page === "peers") {
  $("code").addEventListener("input", () => $("code").value = $("code").value.replace(/\D/g, "").slice(0, 4));
  $("next").addEventListener("click", (e) => {
    if ($("next").classList.contains("disabled")) {
      e.preventDefault();
      $("match-msg").textContent = "マッチが成立すると次へ進めます";
    }
  });
  for (const b of document.querySelectorAll(".segment [data-local]")) {
    b.addEventListener("click", () => showLocalMode(b.dataset.local === "1"));
  }
  $("match").addEventListener("click", async () => {
    // Connecting to the drone takes up to a few seconds: show it, and block double clicks.
    $("match").disabled = true;
    $("match-msg").className = "msg";
    $("match-msg").textContent = localMode ? "2台のドローンにつないでいます…" : "ドローンにつないでいます…";
    const r = await post("/api/match", { player: $("player").value, sn: $("my-drone").value,
                                         code: $("code").value, local: localMode, sn_b: $("my-drone-b").value });
    $("match").disabled = false;
    $("match-msg").className = r.ok ? "msg" : "msg error";
    $("match-msg").textContent = r.ok ? "" : r.message;
    refresh();
  });
  $("unmatch").addEventListener("click", async () => {
    await post("/api/unmatch");
    $("match-msg").textContent = "";
    refresh();
  });
}
if (page === "field") {
  for (const b of document.querySelectorAll(".segment [data-kind]")) {
    b.addEventListener("click", () => { showFieldKind(b.dataset.kind); refresh(); });
  }
  for (const b of document.querySelectorAll(".segment [data-pads]")) {
    b.addEventListener("click", () => { showPadCount(Number(b.dataset.pads)); refresh(); });
  }
  $("simple-save").addEventListener("click", async () => {
    const value = (id) => $(id).value === "" ? null : Number($(id).value);
    const r = await post("/api/arena/simple", { distance: value("s-distance"), back: value("s-back"), side: value("s-side") });
    $("simple-msg").className = r.ok ? "msg ok-text" : "msg error";
    $("simple-msg").textContent = r.ok ? "保存しました。相手のPCにも同じ設定が届きます。" : r.message;
    refresh();
  });
  const stopScan = () => post("/api/arena/scan/stop").then(refresh);
  $("scan-stop").addEventListener("click", stopScan);
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !$("scan-stop").hidden) stopScan(); });
  $("scan-start").addEventListener("click", async () => {
    const value = (id) => $(id).value === "" ? null : Number($(id).value);
    const body = { pads: padCount, back: value("b-back"), front: value("b-front"), far: value("b-far"), near: value("b-near") };
    const origin = padCount === 4 ? 3 : 4;
    if (Object.values(body).some((v) => v === null)) {
      $("scan-msg").className = "msg error";
      $("scan-msg").textContent = `4つの距離を全部入れてください（パッド${origin}から測ったcm）`;
      return;
    }
    if (!confirm(`パッド${origin}の中心から離陸してスキャンします。周りに人や物がないか確認しましたか？`)) return;
    $("scan-msg").className = "msg";
    $("scan-msg").textContent = "離陸します…";
    const r = await post("/api/arena/scan", body);
    $("scan-msg").className = r.ok ? "msg" : "msg error";
    $("scan-msg").textContent = r.ok ? "" : r.message;
    refresh();
  });
  setInterval(refresh, 400);
}
if (page === "battle") {
  for (const b of document.querySelectorAll(".segment button[data-view]")) {
    b.addEventListener("click", () => showView(b.dataset.view));
  }
  for (const b of document.querySelectorAll("#side-switch button")) {
    b.addEventListener("click", () => { side = b.dataset.side; view = null; $("mode-list").dataset.sig = ""; refresh(); });
  }
  $("import-file").addEventListener("change", () => {
    const file = $("import-file").files[0];
    if (file) importFile(file);
    $("import-file").value = "";  // picking the same file again still triggers
  });
  $("ready").addEventListener("click", async () => {
    const r = await post("/api/ready", { ready: $("ready").textContent === "準備OK" });
    if (!r.ok) $("room-status").textContent = r.message;
    refresh();
  });
  $("surrender").addEventListener("click", () => post("/api/surrender").then(refresh));
  $("emergency").addEventListener("click", () => post("/api/emergency").then(refresh));
  $("rematch").addEventListener("click", async () => {
    const r = await post("/api/rematch");
    if (!r.ok) alert(r.message);
    refresh();
  });
  document.addEventListener("keydown", (e) => {
    if ($("live").hidden) return;
    // Esc, Ctrl+C, or ⌘C with nothing selected: give up and land.
    const copyKey = e.key === "c" && (e.ctrlKey || (e.metaKey && !window.getSelection().toString()));
    if (e.key === "Escape" || copyKey) {
      e.preventDefault();
      post("/api/surrender").then(refresh);
    }
  });
  setInterval(refresh, 300);  // smoother countdown and live state
}
if (page === "router" || page === "drones") {
  // Remember the Wi-Fi name (never the password) for the next Tello.
  try { $("ssid").value = localStorage.getItem("ssid") || ""; } catch (e) { /* storage blocked */ }
  $("router").addEventListener("click", async () => {
    $("router-msg").textContent = "送っています…";
    const ssid = $("ssid").value.trim();
    const r = await post("/api/router", { ssid, password: $("password").value });
    $("router-msg").textContent = r.message;
    if (r.ok) {
      $("password").value = "";
      try { localStorage.setItem("ssid", ssid); } catch (e) { /* storage blocked */ }
    }
  });
}

// A reload (not moving between screens) starts everything over.
const navigation = performance.getEntriesByType("navigation")[0];
const startUp = navigation && navigation.type === "reload" ? post("/api/reset") : Promise.resolve();
startUp.then(() => { started = true; refresh(); });
setInterval(refresh, 1500);
