/*
 * Embeddable chat widget. Add it to any dashboard with one tag:
 *   <script src="https://your-host/static/widget.js" data-api="https://your-host" defer></script>
 * It lives in a Shadow DOM, so the host page's CSS can't break it and it can't break the page.
 */
(function () {
  "use strict";
  const script = document.currentScript;
  const API = (script && script.dataset.api) || "";
  const TITLE = (script && script.dataset.title) || "Market Assistant";
  const SUGGESTIONS = [
    "سهم النيل للاتصالات بكام؟",
    "Top gainers in Egypt this week",
    "قارن ATLS و NOVC آخر 3 شهور",
    "Any 52-week breakouts today?",
    "أنهي قطاع كان أحسن الشهر ده؟",
  ];

  const store = {
    get(k) { try { return sessionStorage.getItem(k); } catch (e) { return null; } },
    set(k, v) { try { sessionStorage.setItem(k, v); } catch (e) { /* private mode */ } },
  };

  // ---------- safe Markdown: escape first, then bold, bullets and tables ----------
  function esc(s) {
    return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }
  const inline = (s) => esc(s).replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
  const cells = (row) => row.trim().replace(/^\||\|$/g, "").split("|").map((c) => c.trim());
  function render(md) {
    const lines = md.split("\n");
    let html = "";
    for (let i = 0; i < lines.length; i++) {
      const line = lines[i];
      if (line.trim().startsWith("|") && /^\s*\|?[\s:|-]+\|?\s*$/.test(lines[i + 1] || "")) {
        html += "<div class='tw'><table><thead><tr>" + cells(line).map((c) => `<th>${inline(c)}</th>`).join("") + "</tr></thead><tbody>";
        i += 2;
        while (i < lines.length && lines[i].trim().startsWith("|")) {
          html += "<tr>" + cells(lines[i]).map((c) => `<td>${inline(c)}</td>`).join("") + "</tr>";
          i++;
        }
        html += "</tbody></table></div>";
        i--;
      } else if (/^\s*[-*] /.test(line)) {
        html += "<ul>";
        while (i < lines.length && /^\s*[-*] /.test(lines[i])) {
          html += `<li>${inline(lines[i].replace(/^\s*[-*] /, ""))}</li>`;
          i++;
        }
        html += "</ul>";
        i--;
      } else if (line.trim()) {
        html += `<p>${inline(line)}</p>`;
      }
    }
    return html;
  }

  const CSS = `
    :host { all: initial; }
    * { box-sizing: border-box; font-family: system-ui, "Segoe UI", Tahoma, Arial, sans-serif; }
    .launcher { position: fixed; inset-inline-end: 20px; bottom: 20px; width: 56px; height: 56px; border-radius: 50%;
      border: 0; background: #2563eb; color: #fff; font-size: 24px; cursor: pointer; box-shadow: 0 6px 20px rgba(0,0,0,.25); z-index: 2147483000; }
    .panel { position: fixed; inset-inline-end: 20px; bottom: 88px; width: min(420px, calc(100vw - 32px)); height: min(620px, calc(100vh - 120px));
      display: none; flex-direction: column; border-radius: 16px; overflow: hidden; z-index: 2147483000;
      background: var(--bg); color: var(--fg); box-shadow: 0 12px 40px rgba(0,0,0,.3); border: 1px solid var(--line); }
    .panel.open { display: flex; }
    .light { --bg: #ffffff; --fg: #111827; --muted: #6b7280; --line: #e5e7eb; --bot: #f3f4f6; --me: #2563eb; --err: #b91c1c; --errbg: #fef2f2; }
    .dark { --bg: #111827; --fg: #e5e7eb; --muted: #9ca3af; --line: #374151; --bot: #1f2937; --me: #3b82f6; --err: #fca5a5; --errbg: #3f1d1d; }
    header { display: flex; align-items: center; justify-content: space-between; padding: 12px 14px; border-bottom: 1px solid var(--line); }
    header b { font-size: 15px; } header small { display: block; color: var(--muted); font-size: 12px; }
    header button { background: none; border: 0; color: var(--muted); font-size: 18px; cursor: pointer; }
    .log { flex: 1; overflow-y: auto; padding: 14px; display: flex; flex-direction: column; gap: 10px; font-size: 14px; line-height: 1.55; }
    .msg { max-width: 88%; padding: 9px 12px; border-radius: 14px; overflow-wrap: anywhere; }
    .me { align-self: flex-end; background: var(--me); color: #fff; border-end-end-radius: 4px; white-space: pre-wrap; }
    .bot { align-self: flex-start; background: var(--bot); border-end-start-radius: 4px; }
    .bot.error { background: var(--errbg); color: var(--err); }
    .msg p { margin: 0 0 6px; } .msg p:last-child { margin: 0; } .msg ul { margin: 4px 0; padding-inline-start: 18px; }
    .tw { overflow-x: auto; margin: 6px 0; }
    table { border-collapse: collapse; font-size: 13px; } th, td { border: 1px solid var(--line); padding: 4px 8px; text-align: start; white-space: nowrap; }
    th { background: var(--bg); }
    .cursor::after { content: "▍"; animation: blink 1s steps(1) infinite; } @keyframes blink { 50% { opacity: 0; } }
    details { margin-top: 6px; font-size: 12px; color: var(--muted); } summary { cursor: pointer; }
    code { display: block; white-space: pre-wrap; direction: ltr; text-align: left; font-family: ui-monospace, Consolas, monospace; margin-top: 4px; }
    .chips { display: flex; flex-wrap: wrap; gap: 6px; padding: 0 14px 8px; }
    .chips button { border: 1px solid var(--line); background: var(--bg); color: var(--fg); border-radius: 999px; padding: 5px 10px; font-size: 12px; cursor: pointer; }
    form { display: flex; gap: 8px; padding: 10px 12px; border-top: 1px solid var(--line); }
    input { flex: 1; padding: 9px 11px; border-radius: 10px; border: 1px solid var(--line); background: var(--bot); color: var(--fg); font-size: 14px; }
    form button { border: 0; border-radius: 10px; background: var(--me); color: #fff; padding: 0 14px; cursor: pointer; }
    form button:disabled { opacity: .5; cursor: default; }
    footer { padding: 0 12px 8px; font-size: 11px; color: var(--muted); }`;

  const host = document.createElement("div");
  host.id = "market-assistant-widget";
  const root = host.attachShadow({ mode: "open" });
  const theme = window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  root.innerHTML = `<style>${CSS}</style>
    <button class="launcher" aria-label="Open the market assistant">💬</button>
    <section class="panel ${theme}" role="dialog" aria-label="${esc(TITLE)}">
      <header><div><b>${esc(TITLE)}</b><small>Arabic & English · عربي وإنجليزي</small></div>
        <button class="close" aria-label="Close">✕</button></header>
      <div class="log" aria-live="polite"></div>
      <div class="chips"></div>
      <form><input dir="auto" maxlength="500" autocomplete="off" placeholder="Ask about the market… اسأل عن السوق" aria-label="Your question">
        <button type="submit">Send</button></form>
      <footer>Fictional demo data · information, not investment advice</footer>
    </section>`;
  document.body.appendChild(host);

  const $ = (sel) => root.querySelector(sel);
  const panel = $(".panel"), log = $(".log"), form = $("form"), input = $("input"), send = $("form button");
  $(".launcher").onclick = () => { panel.classList.toggle("open"); input.focus(); };
  $(".close").onclick = () => panel.classList.remove("open");

  function bubble(cls, html) {
    const div = document.createElement("div");
    div.className = "msg " + cls;
    div.dir = "auto";
    div.innerHTML = html;
    log.appendChild(div);
    log.scrollTop = log.scrollHeight;
    return div;
  }

  function trace(div, calls) {
    if (!calls || !calls.length) return;
    const d = document.createElement("details");
    d.innerHTML = "<summary>How I got this</summary>";
    const code = document.createElement("code");
    code.textContent = calls.map((c) => `${c.tool}(${JSON.stringify(c.args)}) → ${c.rows} rows` + (c.sql ? `\n${c.sql}` : "")).join("\n");
    d.appendChild(code);
    div.appendChild(d);
  }

  async function ask(question) {
    bubble("me", esc(question));
    const div = bubble("bot cursor", "");
    send.disabled = true;
    let text = "", meta = null;
    try {
      const res = await fetch(API + "/v1/ask/stream", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: question, session_id: store.get("ma_session") }),
      });
      if (!res.ok || !res.body) {
        const err = await res.json().catch(() => ({}));
        throw new Error((err.detail && err.detail.message) || "The assistant is unavailable right now.");
      }
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        let cut;
        while ((cut = buffer.indexOf("\n\n")) >= 0) {
          const chunk = buffer.slice(0, cut);
          buffer = buffer.slice(cut + 2);
          const data = chunk.split("\n").find((l) => l.startsWith("data:"));
          if (!data) continue;
          const ev = JSON.parse(data.slice(5));
          if (ev.event === "meta") { meta = ev; store.set("ma_session", ev.session_id); }
          if (ev.event === "delta") { text += ev.text; div.innerHTML = render(text); log.scrollTop = log.scrollHeight; }
          if (ev.event === "final") { div.innerHTML = render(ev.answer); trace(div, ev.tool_calls); }
          if (ev.event === "error") { throw new Error(ev.message); }
        }
      }
    } catch (e) {
      div.classList.add("error");
      div.textContent = e.message || "Can't reach the assistant.";
    } finally {
      div.classList.remove("cursor");
      send.disabled = false;
      input.focus();
      log.scrollTop = log.scrollHeight;
    }
  }

  form.addEventListener("submit", (e) => {
    e.preventDefault();
    const q = input.value.trim();
    if (!q || send.disabled) return;
    input.value = "";
    ask(q);
  });
  const chips = $(".chips");
  SUGGESTIONS.forEach((s) => {
    const b = document.createElement("button");
    b.type = "button";
    b.textContent = s;
    b.dir = "auto";
    b.onclick = () => { if (!send.disabled) ask(s); };
    chips.appendChild(b);
  });
  bubble("bot", render("Hi! Ask me about prices, performance, sectors or technical signals.\nأهلًا! اسألني عن الأسعار أو الأداء أو القطاعات أو الإشارات الفنية."));
  window.MarketAssistant = { open: () => panel.classList.add("open"), ask };
})();
