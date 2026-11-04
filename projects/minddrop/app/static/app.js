const state = { user: null, q: "", scope: "all", tag: "", tagColors: {}, draggingId: null };

const $ = (selector) => document.querySelector(selector);

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    credentials: "same-origin",
    ...options,
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.detail || "Something went wrong");
  return data;
}

function formData(form) {
  return Object.fromEntries(new FormData(form).entries());
}

function showApp(user) {
  state.user = user;
  $("#authPanel").classList.add("hidden");
  $("#appPanel").classList.remove("hidden");
  $("#userLine").textContent = `${user.display_name} - ${user.email}`;
  refresh();
}

function showAuth() {
  state.user = null;
  $("#authPanel").classList.remove("hidden");
  $("#appPanel").classList.add("hidden");
}

function chunkHtml(item) {
  const primaryColor = item.tags.map((tag) => state.tagColors[tag]).find(Boolean) || "";
  const colSpan = Math.max(1, Math.min(4, Number(item.col_span || 1)));
  const heightPx = Number(item.height_px || 0);
  const style = [
    primaryColor ? `--chunk-color:${escapeHtml(primaryColor)}` : "",
    `--col-span:${colSpan}`,
    heightPx ? `--card-height:${heightPx}px` : "",
  ].filter(Boolean).join(";");
  const styleAttr = style ? `style="${style}"` : "";
  const tags = item.tags
    .map((tag) => {
      const color = state.tagColors[tag] || "#62d2a2";
      return `<button class="tag" data-tag="${escapeHtml(tag)}" style="--tag-color:${escapeHtml(color)}">#${escapeHtml(tag)}</button>`;
    })
    .join("");
  const href = safeHref(item.source_url);
  const source = href ? `<a href="${escapeHtml(href)}" target="_blank" rel="noreferrer">source</a>` : "";
  const owner = item.visibility === "team" ? "Team" : "Private";
  const pinned = item.pinned ? "true" : "false";
  return `
    <article class="chunk ${item.pinned ? "is-pinned" : ""}" data-id="${item.id}" data-col-span="${colSpan}" data-height-px="${heightPx}" ${styleAttr}>
      <div class="chunk-head">
        <div class="drag-title">
          <button class="drag-handle" data-drag-handle="${item.id}" title="Drag to move">::</button>
          <h3>${escapeHtml(item.title)}</h3>
        </div>
        <div class="chunk-actions">
          <button class="pin ${item.pinned ? "active" : ""}" data-pin="${item.id}" data-pinned="${pinned}" title="Pin note">${item.pinned ? "Pinned" : "Pin"}</button>
          <span>${owner}</span>
          <button class="delete" data-delete="${item.id}" title="Delete chunk">Delete</button>
        </div>
      </div>
      <p class="summary">${escapeHtml(item.summary || "")}</p>
      <div class="body">${renderBody(item)}</div>
      <div class="chunk-foot">
        <div class="tags">${tags}</div>
        <div class="meta">${new Date(item.created_at).toLocaleString()} ${source}</div>
      </div>
      <button class="resize-handle" data-resize="${item.id}" title="Resize note"></button>
    </article>
  `;
}

function renderBody(item) {
  const lines = String(item.body || "").split(/\r?\n/);
  const hasList = lines.some(isListLine);
  if (!hasList) return escapeHtml(item.body);
  return lines
    .map((line, index) => {
      const parsed = parseListLine(line);
      if (!parsed) return `<p class="body-line">${escapeHtml(line)}</p>`;
      const checked = Boolean(item.checklist && item.checklist[String(index)]);
      return `
        <label class="check-row ${checked ? "checked" : ""}">
          <input type="checkbox" data-check="${item.id}" data-line="${index}" ${checked ? "checked" : ""} />
          <span>${escapeHtml(parsed.text)}</span>
        </label>
      `;
    })
    .join("");
}

function isListLine(line) {
  return Boolean(parseListLine(line));
}

function parseListLine(line) {
  const match = String(line).match(/^\s*(?:[-*+]|\d+[.)]|\[[ xX]\])\s+(.+)$/);
  if (!match) return null;
  return { text: match[1] };
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function safeHref(value) {
  try {
    const parsed = new URL(value);
    return ["http:", "https:"].includes(parsed.protocol) ? parsed.href : "";
  } catch {
    return "";
  }
}

async function refresh() {
  const params = new URLSearchParams();
  if (state.q) params.set("q", state.q);
  if (state.scope) params.set("scope", state.scope);
  if (state.tag) params.set("tag", state.tag);
  const [{ chunks }, { tags }, { colors }] = await Promise.all([
    api(`/api/chunks?${params.toString()}`),
    api("/api/tags"),
    api("/api/tag-colors"),
  ]);
  state.tagColors = colors || {};
  $("#chunks").innerHTML = chunks.length ? chunks.map(chunkHtml).join("") : `<div class="empty">Nothing here yet. Capture the first thing on your mind.</div>`;
  renderTagFilters(tags);
  renderColorControls(tags);
}

function renderTagFilters(tags) {
  const tagSelect = $("#tagSelect");
  const selected = tagSelect.value;
  tagSelect.innerHTML = `<option value="">All tags</option>` + tags.map((tag) => `<option value="${escapeHtml(tag)}">#${escapeHtml(tag)}</option>`).join("");
  tagSelect.value = selected;
}

function renderColorControls(tags) {
  const panel = $("#tagColorPanel");
  if (!panel) return;
  panel.innerHTML = tags.length
    ? tags
        .map((tag) => {
          const color = state.tagColors[tag] || "#62d2a2";
          return `
            <label class="color-control">
              <span>#${escapeHtml(tag)}</span>
              <input type="color" value="${escapeHtml(color)}" data-color-tag="${escapeHtml(tag)}" />
            </label>
          `;
        })
        .join("")
    : `<p class="notice">Tags will appear here after you capture notes.</p>`;
}

async function boot() {
  try {
    const { user } = await api("/api/me");
    showApp(user);
  } catch {
    showAuth();
  }
}

$("#loginForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  $("#authNotice").textContent = "";
  try {
    const { user } = await api("/api/login", { method: "POST", body: JSON.stringify(formData(event.currentTarget)) });
    showApp(user);
  } catch (error) {
    $("#authNotice").textContent = error.message;
  }
});

$("#signupForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  $("#authNotice").textContent = "";
  try {
    const { user } = await api("/api/signup", { method: "POST", body: JSON.stringify(formData(event.currentTarget)) });
    showApp(user);
  } catch (error) {
    $("#authNotice").textContent = error.message;
  }
});

$("#logoutButton").addEventListener("click", async () => {
  await api("/api/logout", { method: "POST" });
  showAuth();
});

$("#chunkForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const payload = formData(form);
  payload.tags = payload.tags ? payload.tags.split(",").map((tag) => tag.trim()).filter(Boolean) : [];
  $("#captureNotice").textContent = "Organizing...";
  try {
    await api("/api/chunks", { method: "POST", body: JSON.stringify(payload) });
    form.reset();
    $("#captureNotice").textContent = "Saved.";
    await refresh();
  } catch (error) {
    $("#captureNotice").textContent = error.message;
  }
});

$("#searchInput").addEventListener("input", (event) => {
  state.q = event.target.value.trim();
  window.clearTimeout(window.searchTimer);
  window.searchTimer = window.setTimeout(refresh, 180);
});

$("#scopeSelect").addEventListener("change", (event) => {
  state.scope = event.target.value;
  refresh();
});

$("#tagSelect").addEventListener("change", (event) => {
  state.tag = event.target.value;
  refresh();
});

$("#tagColorPanel").addEventListener("input", async (event) => {
  const input = event.target.closest("[data-color-tag]");
  if (!input) return;
  state.tagColors[input.dataset.colorTag] = input.value;
  paintVisibleCards();
  window.clearTimeout(window.colorTimer);
  window.colorTimer = window.setTimeout(async () => {
    await api(`/api/tag-colors/${encodeURIComponent(input.dataset.colorTag)}`, {
      method: "PATCH",
      body: JSON.stringify({ color: input.value }),
    });
  }, 250);
});

function paintVisibleCards() {
  document.querySelectorAll(".chunk").forEach((card) => {
    const tags = Array.from(card.querySelectorAll("[data-tag]")).map((tag) => tag.dataset.tag);
    const color = tags.map((tag) => state.tagColors[tag]).find(Boolean);
    if (color) card.style.setProperty("--chunk-color", color);
  });
  document.querySelectorAll("[data-tag]").forEach((tag) => {
    tag.style.setProperty("--tag-color", state.tagColors[tag.dataset.tag] || "#62d2a2");
  });
}

$("#chunks").addEventListener("click", (event) => {
  const deleteButton = event.target.closest("[data-delete]");
  if (deleteButton) {
    deleteChunk(deleteButton.dataset.delete);
    return;
  }
  const pinButton = event.target.closest("[data-pin]");
  if (pinButton) {
    togglePin(pinButton.dataset.pin, pinButton.dataset.pinned !== "true");
    return;
  }
  const button = event.target.closest("[data-tag]");
  if (!button) return;
  state.tag = button.dataset.tag;
  $("#tagSelect").value = state.tag;
  refresh();
});

$("#chunks").addEventListener("change", async (event) => {
  const checkbox = event.target.closest("[data-check]");
  if (!checkbox) return;
  const row = checkbox.closest(".check-row");
  row.classList.toggle("checked", checkbox.checked);
  await api(`/api/chunks/${checkbox.dataset.check}/checklist`, {
    method: "PATCH",
    body: JSON.stringify({ line: Number(checkbox.dataset.line), checked: checkbox.checked }),
  });
});

$("#chunks").addEventListener("pointerdown", (event) => {
  const dragHandle = event.target.closest("[data-drag-handle]");
  if (dragHandle) {
    const card = dragHandle.closest(".chunk");
    if (!card) return;
    event.preventDefault();
    startMove(event, card);
    return;
  }
  const handle = event.target.closest("[data-resize]");
  if (!handle) return;
  const card = handle.closest(".chunk");
  if (!card) return;
  event.preventDefault();
  startResize(event, card);
});

function startMove(event, card) {
  const grid = $("#chunks");
  state.draggingId = card.dataset.id;
  card.classList.add("dragging");
  card.style.pointerEvents = "none";

  function onMove(moveEvent) {
    const target = document.elementFromPoint(moveEvent.clientX, moveEvent.clientY)?.closest?.(".chunk");
    if (!target || target === card || !grid.contains(target)) return;
    const box = target.getBoundingClientRect();
    const after = moveEvent.clientY > box.top + box.height / 2;
    grid.insertBefore(card, after ? target.nextSibling : target);
  }

  async function onUp() {
    window.removeEventListener("pointermove", onMove);
    window.removeEventListener("pointerup", onUp);
    card.classList.remove("dragging");
    card.style.pointerEvents = "";
    state.draggingId = null;
    const ids = Array.from(document.querySelectorAll("#chunks .chunk")).map((item) => Number(item.dataset.id));
    await api("/api/chunks/reorder", { method: "POST", body: JSON.stringify({ ids }) });
  }

  window.addEventListener("pointermove", onMove);
  window.addEventListener("pointerup", onUp);
}

function startResize(event, card) {
  const grid = $("#chunks");
  const startX = event.clientX;
  const startY = event.clientY;
  const startSpan = Number(card.dataset.colSpan || 1);
  const startHeight = Number(card.dataset.heightPx || card.getBoundingClientRect().height);
  const gridWidth = grid.getBoundingClientRect().width;
  const columnWidth = Math.max(180, gridWidth / 4);
  const minHeight = 150;
  card.classList.add("resizing");

  function onMove(moveEvent) {
    const deltaX = moveEvent.clientX - startX;
    const deltaY = moveEvent.clientY - startY;
    const nextSpan = Math.max(1, Math.min(4, Math.round(startSpan + deltaX / columnWidth)));
    const nextHeight = Math.max(minHeight, Math.min(1200, Math.round(startHeight + deltaY)));
    card.dataset.colSpan = String(nextSpan);
    card.dataset.heightPx = String(nextHeight);
    card.style.setProperty("--col-span", nextSpan);
    card.style.setProperty("--card-height", `${nextHeight}px`);
  }

  async function onUp() {
    window.removeEventListener("pointermove", onMove);
    window.removeEventListener("pointerup", onUp);
    card.classList.remove("resizing");
    await api(`/api/chunks/${card.dataset.id}`, {
      method: "PATCH",
      body: JSON.stringify({
        col_span: Number(card.dataset.colSpan || 1),
        height_px: Number(card.dataset.heightPx || 0),
      }),
    });
  }

  window.addEventListener("pointermove", onMove);
  window.addEventListener("pointerup", onUp);
}

async function togglePin(id, pinned) {
  await api(`/api/chunks/${id}`, {
    method: "PATCH",
    body: JSON.stringify({ pinned }),
  });
  refresh();
}

async function deleteChunk(id) {
  if (!window.confirm("Delete this chunk?")) return;
  await api(`/api/chunks/${id}`, { method: "DELETE" });
  refresh();
}

$("#randomButton").addEventListener("click", async () => {
  const { chunk } = await api("/api/random");
  const card = $("#randomCard");
  if (!chunk) return;
  card.classList.remove("hidden");
  card.innerHTML = `<strong>Resurfaced:</strong> ${chunkHtml(chunk)}`;
});

boot();
