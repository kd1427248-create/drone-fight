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
    '<tr><td colspan="4" class="muted">' +
    (s.scanned_at ? "見つかりませんでした。電源・Wi-Fiを確認してください" : "まだ探していません") + "</td></tr>";
  for (const d of s.drones) {
    const tr = document.createElement("tr");
    tr.innerHTML = '<td><input maxlength="20" placeholder="例: ゆーしん機"><div class="saved muted"></div></td>' +
      '<td class="mono"></td><td></td>' +
      '<td><button class="ghost claim"></button></td>';
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
    $("drones").appendChild(tr);
  }
}

let meLoaded = false;

function droneText(d) {
  if (!d) return "ドローン未選択";
  return (d.name || "名前なし") + "（" + (d.ip ? (d.ssid || d.ip) : "未確認") + "）";
}

function renderPeers(s) {
  if (!meLoaded) { $("player").value = s.player; $("code").value = s.code; meLoaded = true; }
  // Drones from the last scan, plus the saved one even if it is not found right now.
  const options = [["", "選んでください"]].concat(s.drones.map((d) => [d.sn, droneLabel(d)]));
  if (s.my_drone && !s.drones.some((d) => d.sn === s.my_drone.sn)) {
    options.push([s.my_drone.sn, (s.my_drone.name || s.my_drone.sn) + "（今は見つかっていません）"]);
  }
  if (fillSelect($("my-drone"), options) && s.my_drone) $("my-drone").value = s.my_drone.sn;

  // Inputs are locked while matching.
  for (const id of ["player", "my-drone", "code"]) $(id).disabled = s.matching;
  $("match").hidden = s.matching;
  $("unmatch").hidden = !s.matching;
  $("status").hidden = !s.matching;

  const opponent = s.matching ? s.peers[0] : null;
  $("waiting").hidden = !s.matching || !!opponent;
  $("matched").hidden = !opponent;
  $("waiting-detail").textContent = "合言葉「" + s.code + "」で探しています。相手にも同じ合言葉でマッチングを始めてもらってください";
  if (opponent) {
    $("me-name").textContent = s.player;
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

function setPlayerCard(prefix, name, drone, ready) {
  $(prefix + "-name").textContent = name || "名前なし";
  $(prefix + "-drone").textContent = drone ? (drone.name || "名前なし") : "ドローン未選択";
  $(prefix + "-ssid").textContent = drone && drone.ssid ? drone.ssid : "Wi-Fi名 未確認";
  $(prefix + "-battery").textContent = "電池 " + batteryText(drone && drone.battery);
  $(prefix + "-battery").className = drone && drone.battery && Number(drone.battery) < 30 ? "error" : "muted";
  $(prefix + "-ready").textContent = ready ? "準備OK" : "準備中…";
  $(prefix + "-ready").className = "ready-badge" + (ready ? " on" : "");
}

function renderModes(s) {
  const box = $("mode-list");
  const sig = JSON.stringify([s.choices, s.plan_key, s.ready]);
  if (box.dataset.sig === sig) return;
  box.dataset.sig = sig;
  box.innerHTML = "";
  for (const c of s.choices) {
    const card = document.createElement("div");
    card.className = "mode-card" + (c.key === s.plan_key ? " on" : "") + (c.imported ? " imported" : "");
    card.tabIndex = 0;
    card.innerHTML = "<b></b><span></span>";
    card.children[0].textContent = c.title;
    card.children[1].textContent = c.description;
    const choose = () => choosePlan(...splitPlan(c.key));
    card.addEventListener("click", choose);
    card.addEventListener("keydown", (e) => { if (e.key === "Enter") choose(); });
    if (c.imported && !s.ready) {
      const remove = document.createElement("button");
      remove.className = "remove";
      remove.title = "このファイルを外す";
      remove.textContent = "×";
      remove.addEventListener("click", async (e) => {
        e.stopPropagation();
        if (!confirm("「" + c.title + "」を外しますか？")) return;
        await post("/api/plans/delete", { name: splitPlan(c.key)[1] });
        box.dataset.sig = "";
        refresh();
      });
      card.appendChild(remove);
    }
    box.appendChild(card);
  }
  const add = document.createElement("button");
  add.className = "mode-card import-card";
  add.innerHTML = "<b>＋ ファイルをインポート</b><span>自分で書いた .txt を読み込む</span>";
  add.addEventListener("click", () => $("import-file").click());
  box.appendChild(add);
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
  const r = await post("/api/plan", { kind, name });
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

function renderBattle(s) {
  const opponent = s.matching ? s.peers[0] : null;
  $("no-match").hidden = !!opponent;
  $("room").hidden = !opponent;
  if (!opponent) return;
  setPlayerCard("me", s.player, s.my_drone, s.ready);
  setPlayerCard("op", opponent.player, opponent.drone, opponent.ready);
  $("limit").textContent = s.battle_seconds;
  $("log").textContent = s.me.log.join("\n");

  const started = !!s.start_at;
  const finished = !!s.result;
  $("setup").hidden = started;
  $("live").hidden = !started || finished;
  $("result").hidden = !finished;

  if (!started) {
    renderModes(s);
    const chosen = s.choices.find((c) => c.key === s.plan_key);
    const planText = chosen ? "作戦: " + chosen.title : "作戦を選んでください";
    $("mode-list").classList.toggle("locked", s.ready);
    $("ready").textContent = s.ready ? "準備OKを取り消す" : "準備OK";
    $("ready").className = "big-button" + (s.ready ? " ghost" : "");
    $("ready").disabled = !s.plan;
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
  $("me-state").textContent = droneState(s.me);
  $("me-plan").textContent = s.me.plan ? "作戦: " + s.me.plan : "";
  $("op-state").textContent = droneState(opponent.battle);
  $("op-plan").textContent = opponent.battle && opponent.battle.plan ? "作戦: " + opponent.battle.plan : "";
  const switcher = $("mode-switch");
  const sig = JSON.stringify([s.choices, s.me.plan_key, s.me.phase]);
  if (switcher.dataset.sig !== sig) {
    switcher.dataset.sig = sig;
    switcher.innerHTML = "";
    for (const c of s.choices) {
      const b = document.createElement("button");
      b.textContent = c.title;
      b.className = c.key === s.me.plan_key ? "" : "ghost";
      b.disabled = s.me.phase !== "flying";
      b.addEventListener("click", () => post("/api/mode", { key: c.key }).then(refresh));
      switcher.appendChild(b);
    }
  }
  $("surrender").disabled = !["starting", "flying"].includes(s.me.phase);

  if (finished) {
    const titles = { win: "勝ち！", lose: "負け…", draw: "引き分け" };
    $("result-title").textContent = titles[s.result.outcome];
    $("result-title").className = "result-title " + s.result.outcome;
    $("result-reason").textContent = s.result.reason;
    $("rematch").disabled = ["starting", "flying"].includes(s.me.phase);
  }
}

async function refresh() {
  let s;
  try { s = await (await fetch("/api/state")).json(); } catch (e) { return; }
  renderHeader(s);
  if (page === "drones") renderDrones(s);
  if (page === "peers") renderPeers(s);
  if (page === "battle") renderBattle(s);
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
  $("match").addEventListener("click", async () => {
    // Connecting to the drone takes up to a few seconds: show it, and block double clicks.
    $("match").disabled = true;
    $("match-msg").className = "msg";
    $("match-msg").textContent = "ドローンにつないでいます…";
    const r = await post("/api/match", { player: $("player").value, sn: $("my-drone").value,
                                         code: $("code").value });
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
if (page === "battle") {
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

refresh();
setInterval(refresh, 1500);
