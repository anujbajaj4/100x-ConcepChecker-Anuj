// Concept Check page logic. Kept in its own file so the Content Security Policy can
// forbid inline scripts: only code served from our own address may run.

const $ = (id) => document.getElementById(id);
const log = $("log"), message = $("message"), send = $("send");

const LOGO = '<svg width="18" height="18" viewBox="0 0 28 28" aria-hidden="true"><circle cx="6" cy="14" r="4" fill="#fff"/><circle cx="22" cy="6" r="3.5" fill="none" stroke="#fff" stroke-width="2.5"/><circle cx="22" cy="22" r="3.5" fill="none" stroke="#fff" stroke-width="2.5"/><path d="M10 14 C14 14 14 6 18.5 6 M10 14 C14 14 14 22 18.5 22" fill="none" stroke="#fff" stroke-width="2.5"/></svg>';
const ICONS = {
  passed: '<svg width="22" height="22" viewBox="0 0 22 22" aria-hidden="true"><circle cx="11" cy="11" r="10" fill="#1F6E46"/><path d="M6.5 11.2 L9.6 14.2 L15.5 8" fill="none" stroke="#fff" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  half: '<svg width="22" height="22" viewBox="0 0 22 22" aria-hidden="true"><circle cx="11" cy="11" r="9.25" fill="#fff" stroke="#8A5A00" stroke-width="1.5"/><path d="M11 1.75 A9.25 9.25 0 0 1 11 20.25 Z" fill="#8A5A00"/></svg>',
  missed: '<svg width="22" height="22" viewBox="0 0 22 22" aria-hidden="true"><circle cx="11" cy="11" r="9.25" fill="#fff" stroke="#A8321C" stroke-width="1.5"/><path d="M7.5 11 H14.5" stroke="#A8321C" stroke-width="2" stroke-linecap="round"/></svg>',
  current: '<svg width="22" height="22" viewBox="0 0 22 22" aria-hidden="true"><circle cx="11" cy="11" r="9.25" fill="#fff" stroke="#1E2B45" stroke-width="2"/><circle cx="11" cy="11" r="4" fill="#1E2B45"/></svg>',
  upcoming: '<svg width="22" height="22" viewBox="0 0 22 22" aria-hidden="true"><circle cx="11" cy="11" r="9.25" fill="none" stroke="#8A93A3" stroke-width="1.5"/></svg>',
};
const CLOSE = '<svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true"><path d="M4 4 L12 12 M12 4 L4 12" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>';
const RESULT = { 1: ["passed", "Passed", "#1F6E46"], 0.5: ["half", "Passed after a nudge", "#8A5A00"], 0: ["missed", "Not yet", "#A8321C"] };
const SEG = { 1: "#1F6E46", 0.5: "#B07A12", 0: "#A8321C" };

// How each status looks on the map.
const STATUS = {
  mastered: { label: "Mastered", strong: "#1F6E46", tint: "#CDE7D6", bar: "#1F6E46" },
  getting: { label: "Getting there", strong: "#8A5A00", tint: "#F4DFA6", bar: "#B07A12" },
  needs: { label: "Needs work", strong: "#A8321C", tint: "#F4CEC4", bar: "#A8321C" },
  untested: { label: "Not tested", strong: "#5E6878", tint: "transparent", bar: "#D5DBE3" },
};
const statusOf = (score) => score == null ? "untested" : score >= 80 ? "mastered" : score >= 50 ? "getting" : "needs";

// --- Login ---
// Your login token proves who you are to our server. The server keeps it in an
// HttpOnly cookie: the browser sends it to /api by itself, and this page's code can
// never read it, so a malicious script injected here couldn't steal it either.
// Nothing about you is saved in this browser's storage.
let email = null;
// A guest tries the app with no account. Their test's state lives in this variable and
// goes to the server with each message; nothing is saved anywhere, and a refresh ends it.
let guest = false, guestState = null;

// Earlier versions kept the token in localStorage. Wipe any copy left behind.
try { localStorage.removeItem("token"); localStorage.removeItem("email"); } catch {}

// Address: /api/login or /api/signup. Action: POST. Package: email and password. Key: none yet.
async function logIn(path) {
  const error = $("login-error");
  error.hidden = true;
  try {
    const r = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email: $("email").value.trim(), password: $("password").value }),
    });
    const data = await r.json().catch(() => ({}));
    if (!r.ok || !data.email) {
      error.textContent = data.message || data.detail || "Something went wrong. Try again.";
      error.hidden = false;
      return;
    }
    email = data.email;
    $("password").value = "";
    showHome();
  } catch {
    error.textContent = "Could not reach the server. Is it running?";
    error.hidden = false;
  }
}

// Logging out asks the server to delete the cookie and end the session at Supabase.
function logOut(reason) {
  fetch("/api/logout", { method: "POST" }).catch(() => {});
  email = null; home = null; firstDraw = true;
  showLogin(reason);
}

// A 401 means the server doesn't know who you are. On first visit that's normal,
// so just show the log-in screen. Mid-session it means your hour is up.
function loggedOut(detail) {
  email = null; home = null; firstDraw = true;
  showLogin(detail === "Log in first." ? "" : detail);
}

$("login-form").onsubmit = (e) => { e.preventDefault(); logIn("/api/login"); };
$("signup").onclick = () => { if ($("login-form").reportValidity()) logIn("/api/signup"); };
// For a guest this button reads "Sign up to save" and goes to the log-in screen.
$("logout").onclick = () => {
  if (!guest) return logOut();
  guest = false; guestState = null; home = null; firstDraw = true;
  showLogin("");
};
$("guest").onclick = () => {
  guest = true; guestState = null; home = null; firstDraw = true;
  $("login-error").hidden = true;
  loadHome();
};

// --- Views ---
function showView(name) {
  for (const id of ["login", "home", "test"]) $(id).hidden = id !== name;
  $("back").hidden = name !== "test";
  $("brand").style.display = name === "test" ? "none" : "";
  $("head-right").hidden = name !== "test";
  $("progress").hidden = name !== "home";
  $("account").hidden = name === "login";
  $("who").textContent = guest ? "Guest" : email || "";
  $("logout").textContent = guest ? "Sign up to save" : "Log out";
  $("topic").hidden = name !== "test";
  if (name !== "test") { $("title").textContent = "Concept Check"; stopVoice(true); }
}

function showLogin(reason) {
  showView("login");
  $("login-error").textContent = reason || "";
  $("login-error").hidden = !reason;
}

// Leaving a test zooms back out into the map, onto the subtopic you just did.
async function showHome() {
  const from = !$("test").hidden && subtopic;
  const cover = from ? zoomCover() : null;
  showView("home");
  returning = !!from;
  await loadHome();
  returning = false;
  if (cover) zoomOut(cover, from);
}

// --- Zoom between the map and a test ---
// One plain box grows from the subtopic's node to fill the screen, the test appears
// under it, and the box fades. Going back runs the same box in reverse.
const ZOOM_MS = 460;
const calm = () => matchMedia("(prefers-reduced-motion: reduce)").matches;
let returning = false;  // true while the map redraws behind a zoom-out

// The copy of a subtopic's node that's on screen now: the map on wide screens, the list on phones.
function nodeOnScreen(title) {
  const s = subs.find((x) => x.title === title);
  if (!s) return null;
  return [...document.querySelectorAll('.sub[data-i="' + s.i + '"]')].find((el) => {
    if (!el.offsetParent) return false;
    const r = el.getBoundingClientRect();
    return r.bottom > 0 && r.top < innerHeight;
  }) || null;
}

function fullRect() {
  const top = document.querySelector(".top").getBoundingClientRect().bottom;
  return { left: 0, top: top, width: innerWidth, height: innerHeight - top };
}

// Where the map should scale from: the node's centre, measured inside the map's own box.
function origin(map, r) {
  const m = map.getBoundingClientRect();
  return (r.left - m.left + r.width / 2) + "px " + (r.top - m.top + r.height / 2) + "px";
}

function box(r) {
  return { left: r.left + "px", top: r.top + "px", width: r.width + "px", height: r.height + "px" };
}

function zoomCover(label) {
  const el = document.createElement("div");
  el.className = "zoom";
  el.setAttribute("aria-hidden", "true");
  el.innerHTML = "<span>" + escape(label || "") + "</span>";
  Object.assign(el.style, box(fullRect()));
  document.body.append(el);
  return el;
}

function zoomIn(fromEl, title, then) {
  const node = nodeOnScreen(title) || fromEl;
  if (calm() || !node) return then();
  const r = node.getBoundingClientRect();
  const s = subs.find((x) => x.title === title);
  const el = zoomCover(title);
  el.style.background = s && s.status !== "untested" ? STATUS[s.status].tint : "var(--card)";
  const map = $("home");
  map.style.transformOrigin = origin(map, r);
  map.animate([{ transform: "scale(1)", opacity: 1 }, { transform: "scale(1.06)", opacity: 0.2 }], { duration: ZOOM_MS, easing: "ease-in" });
  el.animate([{ ...box(r), borderRadius: "8px" }, { ...box(fullRect()), borderRadius: "0px", background: "var(--ground)" }], { duration: ZOOM_MS, easing: "cubic-bezier(0.5, 0, 0.2, 1)", fill: "forwards" });
  el.firstChild.animate([{ opacity: 1 }, { opacity: 0 }], { duration: ZOOM_MS * 0.6, fill: "forwards" });
  setTimeout(() => {
    then();
    el.animate([{ opacity: 1 }, { opacity: 0 }], { duration: 220, fill: "forwards" }).onfinish = () => el.remove();
  }, ZOOM_MS);
}

function zoomOut(el, title) {
  let node = nodeOnScreen(title);
  if (!node) {
    const s = subs.find((x) => x.title === title);
    const any = s && [...document.querySelectorAll('.sub[data-i="' + s.i + '"]')].find((x) => x.offsetParent);
    any?.scrollIntoView({ block: "center" });
    node = nodeOnScreen(title);
  }
  if (calm() || !node) return el.remove();
  const r = node.getBoundingClientRect();
  const s = subs.find((x) => x.title === title);
  const map = $("home");
  map.style.transformOrigin = origin(map, r);
  map.animate([{ transform: "scale(1.06)", opacity: 0.2 }, { transform: "scale(1)", opacity: 1 }], { duration: ZOOM_MS, easing: "ease-out" });
  el.animate([{ ...box(fullRect()), borderRadius: "0px", background: "var(--ground)", opacity: 1 },
    { opacity: 1, offset: 0.7 },
    { ...box(r), borderRadius: "8px", background: s && s.status !== "untested" ? STATUS[s.status].tint : "var(--card)", opacity: 0 }],
    { duration: ZOOM_MS, easing: "cubic-bezier(0.5, 0, 0.2, 1)", fill: "forwards" }).onfinish = () => el.remove();
}

// --- Home: the mind map ---
// Board coordinates, in px, before the board is scaled to fit the screen.
const W = 1190, H = 680, ROW = 54, SUB_X = 600, SUB_W = 300, POP_X = 930;
let home = null;          // the latest /api/home reply
let subs = [];            // every subtopic, flattened, with its position
let selected = null;      // index of the selected subtopic
let filter = null;        // status highlighted from the legend
let focusTopic = null;    // topic whose branch is highlighted
let firstDraw = true;     // play the drawing animation only once per visit

// Read: the topics, subtopics and your scores, in one request.
// Address: /api/home. Action: GET. Key: your login cookie, sent by the browser.
async function loadHome() {
  $("home-status").textContent = home ? "" : "Loading your map…";
  try {
    const r = await fetch(guest ? "/api/guest/home" : "/api/home");
    const data = await r.json();
    if (r.status === 401) return loggedOut(data.detail);
    if (!r.ok) throw new Error(r.status + ": " + (data.detail || "Something went wrong"));
    const previous = home;
    home = data;
    showView("home");
    email = data.email;
    $("who").textContent = guest ? "Guest" : email;
    $("home-status").textContent = "";
    drawHome(previous);
  } catch (e) {
    showView("home");
    $("home-status").textContent = e.message.startsWith("Failed") ? "Could not reach the server. Is it running?" : e.message;
  }
}

function drawHome(previous) {
  $("root-title").textContent = home.title;
  // Flatten subtopics and remember what each scored last time we drew, to animate changes.
  const before = {};
  if (previous) previous.topics.forEach((t) => t.subtopics.forEach((s) => (before[s.title] = s.latest)));
  subs = [];
  home.topics.forEach((t, ti) => t.subtopics.forEach((s) => {
    const i = subs.length;
    subs.push({ ...s, i, ti, topic: t.title, y: 42 + i * ROW, status: statusOf(s.latest), before: s.title in before ? before[s.title] : s.latest });
  }));
  home.topics.forEach((t, ti) => {
    const mine = subs.filter((s) => s.ti === ti);
    t.y = mine.reduce((a, s) => a + s.y, 0) / mine.length;
    const tested = mine.filter((s) => s.latest != null);
    t.weakest = tested.length ? Math.min(...tested.map((s) => s.latest)) : null;
    t.tested = tested.length;
    t.count = mine.length;
  });
  drawProgress();
  drawSuggest();
  drawBoard();
  drawTree();
  applyHighlights();
  if (selected != null) select(selected, false);

  // Anything that changed since the last draw grows to its new score, then pings once.
  const changed = subs.filter((s) => s.before !== s.latest);
  if (changed.length) {
    const s = changed[0];
    toast(`${s.title}: ${s.before == null ? "first score" : s.before + "%"} → ${s.latest}%. ${STATUS[s.status].label}.`);
  }
  // Each fill starts at the old score (or empty on first visit) and grows to the new one.
  setTimeout(() => {
    document.querySelectorAll(".sub[data-i]").forEach((el) => {
      const s = subs[el.dataset.i];
      el.querySelector(".fill").style.width = (s.latest || 0) + "%";
      if (s.before !== s.latest) el.classList.add("updated");
    });
  }, firstDraw ? 500 : returning ? ZOOM_MS + 80 : 60);
  firstDraw = false;
}

function drawProgress() {
  const done = subs.filter((s) => s.latest != null).length;
  $("done-count").textContent = done + " of " + subs.length;
  $("hsegs").innerHTML = subs.map((s) => '<span style="background:' + STATUS[s.status].bar + '" title="' + escape(s.title) + '"></span>').join("");
}

// Suggest one next step: your weakest set below 80%, else the first set you haven't tried.
function drawSuggest() {
  const tested = subs.filter((s) => s.latest != null && s.latest < 80).sort((a, b) => a.latest - b.latest);
  const fresh = subs.find((s) => s.latest == null);
  let pick, text;
  if (guest) {
    pick = subs.find((s) => s.title === "Anatomy of an API Request") || subs.at(0);
    text = `You're exploring as a guest, so nothing is saved. Try <strong>${escape(pick.title)}</strong>: explain it in your own words, then test it on a new case.`;
  } else if (tested.length) {
    pick = tested[0];
    text = `Your weakest set is <strong>${escape(pick.title)}</strong> at ${pick.latest}%. One more go could move it up.`;
  } else if (fresh) {
    pick = fresh;
    text = subs.some((s) => s.latest != null)
      ? `Everything you've tested is at 80% or more. Next up: <strong>${escape(pick.title)}</strong>.`
      : `New here? Start with <strong>${escape(pick.title)}</strong>, the first idea on the map.`;
  } else {
    pick = null;
    text = "Every subtopic is mastered. Retake any set to keep it fresh.";
  }
  subs.forEach((s) => (s.suggested = pick && s.i === pick.i));
  $("suggest").hidden = false;
  $("suggest").innerHTML = "<p>" + text + "</p>" + (pick
    ? '<div class="actions" style="margin:0"><button class="btn" type="button" data-act="show" data-i="' + pick.i + '">Show on map</button><button class="btn" type="button" data-act="practice" data-i="' + pick.i + '">Practise</button><button class="btn primary" type="button" data-act="real" data-i="' + pick.i + '">Real-world test</button></div>'
    : "");
}

function linkPath(x1, y1, x2, y2) {
  const mx = (x1 + x2) / 2;
  return `M${x1} ${y1} C${mx} ${y1} ${mx} ${y2} ${x2} ${y2}`;
}

function drawBoard() {
  const rootY = home.topics.reduce((a, t) => a + t.y, 0) / home.topics.length;
  let svg = '<svg width="' + W + '" height="' + H + '" aria-hidden="true">';
  home.topics.forEach((t, ti) => {
    svg += '<path class="link" pathLength="1" data-t="' + ti + '" style="--d:' + (80 + ti * 60) + 'ms" d="' + linkPath(200, rootY, 280, t.y) + '"/>';
  });
  subs.forEach((s) => {
    svg += '<path class="link" pathLength="1" data-t="' + s.ti + '" data-s="' + s.i + '" style="--d:' + (380 + s.i * 30) + 'ms" d="' + linkPath(510, home.topics[s.ti].y, SUB_X, s.y) + '"/>';
  });
  svg += '<path class="link on" id="pop-link" d=""/></svg>';

  let html = svg + '<div class="node root" style="top:' + (rootY - 42) + 'px;--d:0ms">' + escape(home.title) + "</div>";
  home.topics.forEach((t, ti) => {
    const mine = subs.filter((s) => s.ti === ti);
    const st = STATUS[statusOf(t.weakest)];
    // The three small bars show how many are tested; the text names the weakest score.
    const roll = t.weakest == null ? "Not tested yet" : "Weakest " + t.weakest + "%";
    html += '<button class="node topic" type="button" data-topic="' + ti + '" aria-pressed="' + (focusTopic === ti) + '" style="top:' + (t.y - 36) + 'px;--d:' + (160 + ti * 60) + 'ms" title="Highlight this branch">'
      + '<span class="t">' + escape(t.title) + "</span>"
      + '<span class="roll" style="color:' + st.strong + '"><span class="tri">' + mine.map((s) => '<span style="background:' + STATUS[s.status].bar + '"></span>').join("") + "</span>" + roll + "</span></button>";
  });
  subs.forEach((s) => {
    html += subButton(s, "top:" + (s.y - 22) + "px;--d:" + (420 + s.i * 30) + "ms");
  });
  html += '<div class="pop" id="pop" role="dialog" aria-labelledby="pop-title" hidden></div>';
  const board = $("board");
  board.className = "board" + (firstDraw ? " intro" : "");
  board.innerHTML = html;
  fitBoard();
}

function subButton(s, style) {
  const st = STATUS[s.status];
  return '<button class="node sub' + (s.suggested ? " suggested" : "") + '" type="button" data-i="' + s.i + '" style="' + style + '" aria-label="' + escape(s.title + ", " + (s.latest == null ? "not tested" : s.latest + " percent, " + st.label)) + '">'
    + '<span class="fill" aria-hidden="true" style="width:' + (firstDraw ? 0 : s.before || 0) + "%;background:" + st.tint + '"></span>'
    + '<span class="name">' + escape(s.title) + "</span>"
    + '<span class="score" style="color:' + st.strong + '">' + (s.latest == null ? "Not tested" : s.latest + "%") + "</span></button>";
}

// On phones the map becomes a list: each topic, then its subtopics, with details opening in place.
function drawTree() {
  $("tree").innerHTML = home.topics.map((t, ti) => {
    const st = STATUS[statusOf(t.weakest)];
    return '<section class="tree-topic"><h3>' + escape(t.title) + '</h3><span class="roll" style="color:' + st.strong + '">'
      + (t.weakest == null ? "Not tested yet" : "Weakest " + t.weakest + "%") + "</span>"
      + subs.filter((s) => s.ti === ti).map((s) => subButton(s, "") + '<div class="detail" data-detail="' + s.i + '" hidden></div>').join("")
      + "</section>";
  }).join("");
}

// Scale the board down to fit narrower screens, without ever scaling it up.
function fitBoard() {
  const wrap = $("board-wrap");
  const room = wrap.clientWidth - 48;
  const k = Math.min(1, room / W);
  $("stage").style.width = W * k + "px";
  $("stage").style.height = H * k + "px";
  $("board").style.transform = "scale(" + k + ")";
}
new ResizeObserver(() => { if (home && !$("home").hidden) fitBoard(); }).observe($("board-wrap"));

function popContent(s) {
  const st = STATUS[s.status];
  let status = "Not tested yet";
  if (s.latest != null) {
    status = st.label + ": latest " + s.latest + "%";
    if (s.best > s.latest) status += ", best " + s.best + "%";
    status += ", " + s.tests + (s.tests === 1 ? " test" : " tests");
  }
  const practised = s.practised ? "Practised " + s.practised + (s.practised === 1 ? " time" : " times") : "Not practised yet";
  return '<div class="pop-head"><div><p class="pop-topic">' + escape(s.topic) + '</p><h3 id="pop-title">' + escape(s.title) + "</h3>"
    + '<p class="pop-status" style="color:' + st.strong + '">' + status + '</p></div><button class="x" type="button" data-act="close" aria-label="Close">' + CLOSE + "</button></div>"
    + '<p class="pop-covers">' + s.ideas.map(escape).join("<br>") + "</p>"
    + '<div class="pop-actions">'
    + (s.notes_url
      ? '<a class="pop-btn" href="' + escape(s.notes_url) + '" target="_blank" rel="noopener"><b>Revise</b><small>Open the notes in a new tab</small></a>'
      : '<span class="pop-btn off"><b>Revise</b><small>Notes for this one are coming soon</small></span>')
    + '<button class="pop-btn" type="button" data-act="practice" data-i="' + s.i + '"><b>Practise</b><small>' + practised + ". Aarav's world, never scored</small></button>"
    + '<button class="pop-btn primary" type="button" data-act="real" data-i="' + s.i + '"><b>' + (s.latest == null ? "Real-world test" : "Retake real-world test") + "</b><small>Everyday apps. This one counts</small></button>"
    + "</div>";
}

function select(i, focusTest = true) {
  selected = i;
  const s = subs[i];
  document.querySelectorAll(".sub").forEach((el) => el.classList.toggle("is-selected", +el.dataset.i === i));
  document.querySelectorAll(".link[data-t]").forEach((el) => {
    el.classList.toggle("on", el.dataset.s ? +el.dataset.s === i : +el.dataset.t === s.ti);
  });
  // Map: the popover sits beside the subtopic, kept inside the board.
  const pop = $("pop");
  pop.innerHTML = popContent(s);
  pop.hidden = false;
  const top = Math.max(0, Math.min(H - pop.offsetHeight, s.y - 64));
  pop.style.top = top + "px";
  const anchor = Math.min(top + pop.offsetHeight - 24, Math.max(top + 24, s.y));
  $("pop-link").setAttribute("d", linkPath(SUB_X + SUB_W, s.y, POP_X, anchor));
  // Phone list: the details open under the subtopic.
  document.querySelectorAll("[data-detail]").forEach((el) => {
    el.hidden = +el.dataset.detail !== i;
    if (!el.hidden) el.innerHTML = popContent(s);
  });
  if (focusTest) pop.querySelector('[data-act="practice"]')?.focus({ preventScroll: true });
}

function closePop() {
  if (selected == null) return;
  const i = selected;
  selected = null;
  $("pop").hidden = true;
  $("pop-link").setAttribute("d", "");
  document.querySelectorAll(".sub").forEach((el) => el.classList.remove("is-selected"));
  document.querySelectorAll(".link[data-t]").forEach((el) => el.classList.remove("on"));
  document.querySelectorAll("[data-detail]").forEach((el) => (el.hidden = true));
  document.querySelector('.board .sub[data-i="' + i + '"]')?.focus({ preventScroll: true });
}

// Dim everything outside the legend colour or the topic branch you picked.
function applyHighlights() {
  document.querySelectorAll(".chip").forEach((c) => c.setAttribute("aria-pressed", c.dataset.status === filter));
  const keep = (s) => (!filter || s.status === filter) && (focusTopic == null || s.ti === focusTopic);
  document.querySelectorAll(".sub[data-i]").forEach((el) => el.classList.toggle("dim", !keep(subs[el.dataset.i])));
  document.querySelectorAll(".topic").forEach((el) => {
    const ti = +el.dataset.topic;
    el.setAttribute("aria-pressed", focusTopic === ti);
    el.classList.toggle("dim", !subs.some((s) => s.ti === ti && keep(s)));
  });
  document.querySelectorAll(".link[data-s]").forEach((el) => el.classList.toggle("dim", !keep(subs[el.dataset.s])));
  document.querySelectorAll(".link[data-t]:not([data-s])").forEach((el) => el.classList.toggle("dim", !subs.some((s) => s.ti === +el.dataset.t && keep(s))));
}

// One click handler for the whole home view.
$("home").addEventListener("click", (e) => {
  const chip = e.target.closest(".chip");
  if (chip) { filter = filter === chip.dataset.status ? null : chip.dataset.status; return applyHighlights(); }
  const topic = e.target.closest(".topic");
  if (topic) { focusTopic = focusTopic === +topic.dataset.topic ? null : +topic.dataset.topic; return applyHighlights(); }
  const act = e.target.closest("[data-act]");
  if (act) {
    if (act.dataset.act === "close") return closePop();
    if (act.dataset.act === "practice" || act.dataset.act === "real") {
      const title = subs[act.dataset.i].title;
      return zoomIn(act, title, () => start(title, act.dataset.act));
    }
    if (act.dataset.act === "show") {
      select(+act.dataset.i);
      // Scroll to whichever copy is on screen: the map on wide screens, the list on phones.
      const shown = [...document.querySelectorAll('.sub[data-i="' + act.dataset.i + '"]')].find((el) => el.offsetParent);
      return shown?.scrollIntoView({ block: "center" });
    }
  }
  const sub = e.target.closest(".sub[data-i]");
  if (sub) return selected === +sub.dataset.i ? closePop() : select(+sub.dataset.i);
  if (e.target.closest("#board") && !e.target.closest("#pop")) closePop();
});

// Hovering a subtopic traces its path back to the root.
$("board").addEventListener("mouseover", (e) => {
  const sub = e.target.closest(".sub[data-i]");
  document.querySelectorAll(".link.hover").forEach((el) => el.classList.remove("hover"));
  if (!sub) return;
  const s = subs[sub.dataset.i];
  document.querySelectorAll('.link[data-s="' + s.i + '"], .link[data-t="' + s.ti + '"]:not([data-s])').forEach((el) => el.classList.add("hover"));
});
$("board").addEventListener("mouseleave", () => document.querySelectorAll(".link.hover").forEach((el) => el.classList.remove("hover")));

// Keyboard: up and down move between subtopics, Escape closes the details.
$("home").addEventListener("keydown", (e) => {
  if (e.key === "Escape") return closePop();
  const sub = e.target.closest(".board .sub[data-i]");
  if (!sub || (e.key !== "ArrowDown" && e.key !== "ArrowUp")) return;
  e.preventDefault();
  const next = (+sub.dataset.i + (e.key === "ArrowDown" ? 1 : subs.length - 1)) % subs.length;
  const el = document.querySelector('.board .sub[data-i="' + next + '"]');
  el.focus();
  select(next, false);
});

function toast(text) {
  const t = $("toast");
  t.textContent = text;
  t.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => (t.hidden = true), 4500);
}

// --- Test: the chat ---
let testId = null, subtopic = null, mode = "real";
let pasted = false;  // did the learner paste into the answer box since their last send?

function start(name, segment) {
  subtopic = name;
  mode = segment;
  testId = null;
  guestState = null;
  log.innerHTML = "";
  showView("test");
  $("title").textContent = name;
  talk("start", false);
}

// Create: send one message, get the replies and the test's state back.
// Address: /api/chat. Action: POST. Package: JSON. Key: your login cookie, never the Groq key.
async function talk(text, show = true, simpler = false) {
  if (show) addMine(text);
  const waiting = addRow('<p class="thinking">' + (show ? "Checking your answer…" : simpler ? "Finding a simpler way to ask…" : "Loading the first idea…") + "</p>");
  const sentPasted = pasted;
  pasted = false;
  send.disabled = true;
  try {
    // A guest's request carries their test's state; a learner's carries the test id.
    const r = await fetch(guest ? "/api/guest/chat" : "/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(guest
        ? { state: guestState, subtopic: subtopic, mode: mode, message: text, simpler: simpler, pasted: sentPasted }
        : { test_id: testId, subtopic: subtopic, mode: mode, message: text, simpler: simpler, pasted: sentPasted }),
    });
    const data = await r.json();
    waiting.remove();
    if (r.status === 401) {
      send.disabled = false;
      return loggedOut(data.detail);
    }
    if (!r.ok) {
      addRow('<p class="error">' + escape(r.status + ": " + (data.detail || "Something went wrong")) + "</p>");
    } else {
      if (guest) guestState = data.guest_state;
      else testId = data.test_id;
      data.messages.forEach(addBot);
      drawState(data.state);
      scrollDown();
    }
  } catch {
    waiting.remove();
    addRow('<p class="error">Could not reach the server. Is it running?</p>');
  }
  send.disabled = false;
  message.focus();
}

function addRow(inner, withAvatar = true) {
  const row = document.createElement("div");
  row.className = "row";
  row.innerHTML = (withAvatar ? '<span class="avatar">' + LOGO + "</span>" : "<span></span>") + '<div class="body">' + inner + "</div>";
  log.append(row);
  scrollDown();
  return row;
}

function addMine(text) {
  const div = document.createElement("div");
  div.className = "mine";
  div.textContent = text;
  log.append(div);
  scrollDown();
}

// Wide screens: scroll the chat panel to the newest message.
// Narrow screens: the panel grows with the page, so scroll the page instead.
function scrollDown() {
  if (log.scrollHeight > log.clientHeight + 1) log.scrollTop = log.scrollHeight;
  else window.scrollTo(0, document.documentElement.scrollHeight);
}

function addBot(m) {
  if (m.kind === "intro") return addRow(render(m.text));
  if (m.kind === "explain") {
    log.querySelectorAll(".next-actions, .simpler-actions").forEach((el) => el.remove());
    const row = addRow('<span class="label">' + escape(m.label) + "</span>" + render(m.text)
      + (m.can_simplify ? '<div class="actions simpler-actions"><button class="btn" type="button">Make it simpler</button></div>' : ""));
    const button = row.querySelector(".simpler-actions button");
    if (button) button.onclick = () => { button.parentElement.remove(); talk("Make it simpler", false, true); };
    return;
  }
  if (m.kind === "case") {
    log.querySelectorAll(".simpler-actions").forEach((el) => el.remove());
    return addRow('<span class="label">' + escape(m.label) + "</span>" + render(m.text));
  }
  if (m.kind === "next") {
    const row = addRow(render(m.text) + '<div class="actions next-actions"><button class="btn" type="button">Next idea</button></div>', false);
    row.querySelector("button").onclick = (e) => { e.target.disabled = true; talk("next"); };
    return;
  }
  let inner = '<p class="heading">' + escape(m.heading) + "</p>";
  if (m.text) inner += render(m.text);
  if (m.kind === "follow_up") {
    // Show the sentence here too: by now it has usually scrolled out of view above.
    if (m.quote) inner += '<p class="said">You said: <mark class="recited">' + escape(m.quote) + "</mark></p>";
    inner += '<p class="ask">' + escape(m.question) + "</p>";
    if (m.quote) markQuote(m.quote);
  }
  if (m.kind === "missed") {
    inner += '<p class="heading">The answers</p><ul>' + m.key_points.map((k) =>
      "<li>" + escape(k.text) + (k.had ? ' <span class="had">(you had this)</span>' : "") + "</li>").join("") + "</ul>";
  }
  // A finished practice set points to the real-world test; a finished test points to the map.
  const doneActions = m.mode === "practice"
    ? '<div class="actions"><button class="btn primary" type="button" data-act="real">Take the real-world test</button><button class="btn" type="button" data-act="map">Back to the map</button></div>'
    : '<div class="actions"><button class="btn primary" type="button" data-act="map">See it on the map</button><button class="btn" type="button" data-act="practice">Practise again</button></div>';
  const row = addRow('<div class="card ' + m.kind + '">' + inner + (m.kind === "done" ? doneActions : "") + "</div>", false);
  if (m.kind === "done") {
    row.querySelector('[data-act="map"]').onclick = showHome;
    row.querySelector('[data-act="real"], [data-act="practice"]').onclick = (e) => start(subtopic, e.target.dataset.act);
  }
}

// Underline the quoted sentence inside the message where you wrote it.
function markQuote(quote) {
  const bubble = [...log.querySelectorAll(".mine")].reverse().find((b) => b.textContent.includes(quote));
  if (!bubble) return;
  const text = bubble.textContent, i = text.indexOf(quote);
  bubble.innerHTML = escape(text.slice(0, i)) + '<mark class="recited">' + escape(quote) + "</mark>" + escape(text.slice(i + quote.length));
}

function drawState(s) {
  $("topic").textContent = s.mode_label + ": " + s.topic;
  $("title").textContent = s.subtopic;
  const total = s.ideas.length;
  $("count").innerHTML = s.done ? "Set complete" : "Idea <strong>" + (s.index + 1) + "</strong> of " + total;
  $("score-panel").hidden = s.mode === "practice";
  $("practice-panel").hidden = s.mode !== "practice";
  $("set").innerHTML = s.ideas.map((c, i) => {
    let icon, cls, state = "";
    if (i < s.results.length) {
      const [key, label, color] = RESULT[s.results[i]];
      icon = ICONS[key]; cls = ""; state = '<span class="state" style="color:' + color + '">' + label + "</span>";
    } else if (i === s.index && !s.done) {
      icon = ICONS.current; cls = "current"; state = '<span class="state" style="color:#3A4559">' + (s.waiting ? "Up next" : "You're here") + "</span>";
    } else {
      icon = ICONS.upcoming; cls = "upcoming";
    }
    return '<li class="' + cls + '"' + (cls === "current" ? ' aria-current="step"' : "") + ">" + icon + '<span><span class="name">' + (i + 1) + ". " + escape(c) + "</span>" + state + "</span></li>";
  }).join("");
  $("score").textContent = s.score;
  $("score-of").textContent = "out of " + s.results.length + " answered";
  $("segs").innerHTML = s.ideas.map((_, i) => {
    const color = i < s.results.length ? SEG[s.results[i]] : i === s.index && !s.done ? "#1E2B45" : "";
    return "<span" + (color ? ' style="background:' + color + '"' : "") + "></span>";
  }).join("");
  $("notes").hidden = !s.notes_url;
  $("notes").href = s.notes_url || "#";
  $("end").hidden = s.done;
}

$("end").onclick = () => {
  if (confirm(mode === "practice" ? "Leave this practice set?" : "End this test? Answers so far are saved, but the test won't get a score.")) showHome();
};
$("back").onclick = (e) => { e.preventDefault(); showHome(); };

$("form").onsubmit = (e) => {
  e.preventDefault();
  const text = message.value.trim();
  if (!text || send.disabled || recorder) return;
  message.value = "";
  hint.textContent = HINT;
  talk(text);
};

// A pasted answer is recorded with it, so we can tell copied text from typed reasoning.
message.addEventListener("paste", () => { pasted = true; });

// Enter sends, Shift+Enter adds a line.
message.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    $("form").requestSubmit();
  }
});

// --- Voice: answer out loud ---
// The mic records you, the server turns the audio into words with Groq's speech-to-text,
// and the words land in the answer box. You read them, fix anything it misheard, then send.
const mic = $("mic"), hint = $("hint");
const HINT = hint.textContent;
const VOICE_LIMIT_MS = 110000;   // the server takes about two minutes at most
let recorder = null, voiceTimer = null, dropVoice = false;

// Only offer the mic where the browser can record.
mic.hidden = !(navigator.mediaDevices?.getUserMedia && window.MediaRecorder);

function voiceType() {
  return ["audio/webm;codecs=opus", "audio/webm", "audio/mp4", "audio/ogg"].find((t) => MediaRecorder.isTypeSupported(t)) || "";
}

async function startVoice() {
  let stream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  } catch {
    return toast("Concept Check can't use your microphone. Allow it in the browser's address bar, or type your answer.");
  }
  const type = voiceType();
  const chunks = [];
  recorder = new MediaRecorder(stream, type ? { mimeType: type } : {});
  recorder.ondataavailable = (e) => e.data.size && chunks.push(e.data);
  recorder.onstop = () => {
    stream.getTracks().forEach((t) => t.stop());
    clearInterval(voiceTimer);
    recorder = null;
    mic.setAttribute("aria-pressed", "false");
    mic.setAttribute("aria-label", "Answer out loud");
    if (dropVoice) { dropVoice = false; hint.textContent = HINT; return; }
    transcribe(new Blob(chunks, { type: (type || chunks[0]?.type || "audio/webm").split(";")[0] }));
  };
  recorder.start();
  mic.setAttribute("aria-pressed", "true");
  mic.setAttribute("aria-label", "Stop recording");
  const began = Date.now();
  const tick = () => {
    const secs = Math.round((Date.now() - began) / 1000);
    hint.textContent = "Listening… " + secs + "s. Press the mic again when you're done.";
    if (Date.now() - began > VOICE_LIMIT_MS) stopVoice();
  };
  tick();
  voiceTimer = setInterval(tick, 500);
}

// Send the recording to our server, never to Groq directly: the key stays on the server.
// Address: /api/transcribe. Action: POST. Package: the audio itself.
async function transcribe(audio) {
  mic.disabled = true;
  hint.textContent = "Turning your voice into text…";
  try {
    const r = await fetch("/api/transcribe", { method: "POST", headers: { "Content-Type": audio.type }, body: audio });
    const data = await r.json();
    if (!r.ok) throw new Error(data.detail || "Something went wrong");
    if (!data.text) throw new Error("I didn't catch any words. Try again a little closer to the mic.");
    message.value = (message.value.trim() ? message.value.trim() + " " : "") + data.text;
    message.focus();
    message.setSelectionRange(message.value.length, message.value.length);
    hint.textContent = "Check the words, fix anything it misheard, then send.";
  } catch (e) {
    toast(e.message.startsWith("Failed") ? "Could not reach the server. Type your answer instead." : e.message);
    hint.textContent = HINT;
  }
  mic.disabled = false;
}

// Leaving the test throws a recording away instead of transcribing it.
function stopVoice(drop = false) {
  if (!recorder || recorder.state === "inactive") return;
  dropVoice = drop;
  recorder.stop();
}

mic.onclick = () => (recorder ? stopVoice() : startVoice());

function escape(s) {
  return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

// Just enough Markdown for the replies: **bold**, *italic* and "- list".
// Escape first, so nothing in a reply can run as HTML.
function render(text) {
  const inline = (s) => escape(s).replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>").replace(/\*(.+?)\*/g, "<em>$1</em>");
  return text.split(/\n{2,}/).map((block) => {
    let html = "", lines = [], items = [];
    const flush = () => {
      if (lines.length) html += "<p>" + lines.map(inline).join("<br>") + "</p>";
      if (items.length) html += "<ul>" + items.map((i) => "<li>" + inline(i) + "</li>").join("") + "</ul>";
      lines = []; items = [];
    };
    for (const line of block.split("\n")) {
      if (line.startsWith("- ")) { if (lines.length) flush(); items.push(line.slice(2)); }
      else { if (items.length) flush(); lines.push(line); }
    }
    flush();
    return html;
  }).join("");
}

// Ask the server who you are before showing anything: the map if you're logged in,
// the log-in screen if it says 401. Nothing flashes up in between.
loadHome();
