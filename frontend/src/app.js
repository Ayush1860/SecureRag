// SecureRAG Dashboard Application Logic
document.addEventListener("DOMContentLoaded", () => {
  let currentRole = "employee";
  let rolePolicies = {};

  const backendStatusEl = document.getElementById("backend-status");
  const roleSelectorGrid = document.getElementById("role-selector");
  const activeClearanceDisplay = document.getElementById("active-clearance-display");
  const activeRoleBadge = document.getElementById("active-role-badge");
  const queryForm = document.getElementById("query-form");
  const queryInput = document.getElementById("query-input");
  const topKSlider = document.getElementById("top-k-slider");
  const topKVal = document.getElementById("top-k-val");
  const submitBtn = document.getElementById("submit-btn");
  const btnText = document.getElementById("btn-text");
  const btnSpinner = document.getElementById("btn-spinner");

  // Telemetry elements
  const statRetrieved = document.getElementById("stat-retrieved");
  const statAuthorized = document.getElementById("stat-authorized");
  const statBlocked = document.getElementById("stat-blocked");
  const statFlagged = document.getElementById("stat-flagged");
  const statFlaggedSub = document.getElementById("stat-flagged-sub");
  const statLatency = document.getElementById("stat-latency");
  const statRequestId = document.getElementById("stat-request-id");
  const threatAlert = document.getElementById("threat-alert");

  // Output elements
  const responseBody = document.getElementById("response-body");
  const sourceCountBadge = document.getElementById("source-count-badge");
  const sourcesList = document.getElementById("sources-list");
  const tabSourcesCount = document.getElementById("tab-sources-count");
  const auditTbody = document.getElementById("audit-tbody");

  // Top-k slider sync
  topKSlider.addEventListener("input", (e) => {
    topKVal.textContent = e.target.value;
  });

  // Role switching
  function setRole(newRole) {
    currentRole = newRole;
    document.querySelectorAll(".role-card").forEach((btn) => {
      btn.classList.toggle("active", btn.dataset.role === newRole);
    });

    const policy = rolePolicies[newRole];
    if (policy) {
      activeClearanceDisplay.textContent = `${policy.max_clearance.toUpperCase()} (${policy.departments.join(", ")})`;
      activeRoleBadge.textContent = `Role: ${newRole.replace("_", " ").toUpperCase()}`;
    }
  }

  roleSelectorGrid.addEventListener("click", (e) => {
    const card = e.target.closest(".role-card");
    if (card && card.dataset.role) {
      setRole(card.dataset.role);
    }
  });

  // Presets
  document.querySelectorAll(".preset-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      const q = btn.dataset.query;
      const r = btn.dataset.role;
      if (q) queryInput.value = q;
      if (r) setRole(r);
      executeCurrentQuery();
    });
  });

  // Tabs
  document.querySelectorAll(".tab-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".tab-btn").forEach((b) => b.classList.remove("active"));
      document.querySelectorAll(".tab-content").forEach((c) => c.classList.add("hidden"));
      btn.classList.add("active");
      const target = document.getElementById(btn.dataset.tab);
      if (target) target.classList.remove("hidden");
      if (btn.dataset.tab === "tab-audit") fetchAuditLogs();
    });
  });

  // Initialize
  async function init() {
    try {
      const [healthRes, rolesRes] = await Promise.all([
        fetch("/api/health"),
        fetch("/api/roles")
      ]);
      if (healthRes.ok) {
        const health = await healthRes.json();
        backendStatusEl.textContent = `Online (${health.chunks} chunks indexed)`;
      } else {
        backendStatusEl.textContent = "Degraded";
      }

      if (rolesRes.ok) {
        rolePolicies = await rolesRes.json();
        setRole(currentRole);
      }
    } catch (err) {
      console.error("Failed to connect to backend:", err);
      backendStatusEl.textContent = "Offline / Error";
    }

    fetchAuditLogs();
  }

  // Execute Query
  async function executeCurrentQuery() {
    const q = queryInput.value.trim();
    if (!q) return;

    submitBtn.disabled = true;
    btnText.textContent = "Processing...";
    btnSpinner.classList.remove("hidden");

    try {
      const startClient = performance.now();
      const res = await fetch("/api/query", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          query: q,
          role: currentRole,
          top_k: parseInt(topKSlider.value, 10),
        }),
      });

      const clientLatency = Math.round(performance.now() - startClient);

      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: "Request failed" }));
        throw new Error(err.detail || `Server returned ${res.status}`);
      }

      const data = await res.json();
      renderResults(data, clientLatency);
      fetchAuditLogs();
    } catch (err) {
      responseBody.innerHTML = `<div class="threat-alert"><div class="threat-content"><h4>Error Executing Query</h4><p>${escapeHtml(err.message)}</p></div></div>`;
    } finally {
      submitBtn.disabled = false;
      btnText.textContent = "Execute Query";
      btnSpinner.classList.add("hidden");
    }
  }

  queryForm.addEventListener("submit", (e) => {
    e.preventDefault();
    executeCurrentQuery();
  });

  function renderResults(data, clientLatency) {
    // Telemetry updates
    statRetrieved.textContent = data.retrieved;
    statAuthorized.textContent = data.authorized;
    statBlocked.textContent = data.blocked;

    if (data.flagged_chunks > 0) {
      statFlagged.textContent = `${data.flagged_chunks} Detected`;
      statFlagged.className = "telem-value color-warning";
      statFlaggedSub.textContent = "Quarantined & Sanitized";
      threatAlert.classList.remove("hidden");
    } else {
      statFlagged.textContent = "0 Safe";
      statFlagged.className = "telem-value color-success";
      statFlaggedSub.textContent = "Clean Context";
      threatAlert.classList.add("hidden");
    }

    statLatency.textContent = `${Math.round(data.latency_ms)} ms`;
    statRequestId.textContent = `Req: ${data.request_id.slice(0, 8)}`;

    // Response body
    const formattedAnswer = formatAnswer(data.answer);
    responseBody.innerHTML = formattedAnswer;

    // Badges
    sourceCountBadge.textContent = `${data.sources ? data.sources.length : 0} Sources`;
    tabSourcesCount.textContent = data.sources ? data.sources.length : 0;

    // Render Sources
    if (!data.context_excerpts || data.context_excerpts.length === 0) {
      if (data.authorized === 0) {
        sourcesList.innerHTML = `<p class="empty-hint">All retrieved chunks (${data.blocked} candidate chunks) were blocked by RBAC policy for role <code>${data.role}</code>.</p>`;
      } else {
        sourcesList.innerHTML = `<p class="empty-hint">No source snippets available.</p>`;
      }
    } else {
      sourcesList.innerHTML = data.context_excerpts.map((doc, idx) => {
        const isPoisoned = doc.flagged;
        return `
          <div class="source-item">
            <div class="source-header">
              <span class="source-filename">${escapeHtml(doc.source || `Chunk #${idx + 1}`)}</span>
              <div class="source-badges">
                <span class="clearance-pill pill-${(doc.clearance || 'public').toLowerCase()}">${escapeHtml(doc.clearance || 'public')}</span>
                <span class="info-tag">${escapeHtml(doc.department || 'general')}</span>
                ${isPoisoned ? '<span class="preset-tag tag-warning">Quarantined</span>' : ''}
              </div>
            </div>
            <div class="source-snippet ${isPoisoned ? 'quarantined' : ''}">
              ${escapeHtml(doc.text)}
            </div>
          </div>
        `;
      }).join("");
    }
  }

  async function fetchAuditLogs() {
    try {
      const res = await fetch("/api/audit?limit=15");
      if (!res.ok) return;
      const events = await res.json();
      if (!events || events.length === 0) {
        auditTbody.innerHTML = `<tr><td colspan="8" class="text-center">No audit records logged yet.</td></tr>`;
        return;
      }

      auditTbody.innerHTML = events.map((ev) => {
        const timeStr = ev.timestamp_iso ? ev.timestamp_iso.slice(11, 19) : new Date(ev.timestamp * 1000).toLocaleTimeString();
        const threatBadge = ev.flagged_chunks > 0 
          ? `<span class="preset-tag tag-warning">${ev.flagged_chunks} flagged</span>` 
          : `<span class="preset-tag tag-success">clean</span>`;
        return `
          <tr>
            <td>${escapeHtml(timeStr)}</td>
            <td><code>${escapeHtml(ev.user_role)}</code></td>
            <td><code>${escapeHtml(ev.query_hash)}</code></td>
            <td>${ev.num_retrieved}</td>
            <td>${ev.authorized_count} / <span class="color-warning">${ev.blocked_count}</span></td>
            <td>${threatBadge}</td>
            <td>${Math.round(ev.latency_ms)}ms</td>
            <td><span class="badge ${ev.status === 'success' ? 'badge-cyan' : 'badge-accent'}">${escapeHtml(ev.status)}</span></td>
          </tr>
        `;
      }).join("");
    } catch (e) {
      console.warn("Could not fetch audit logs:", e);
    }
  }

  function formatAnswer(text) {
    if (!text) return "<p>No answer returned.</p>";
    return text
      .split("\n\n")
      .map((p) => `<p>${escapeHtml(p).replace(/\n/g, "<br>")}</p>`)
      .join("");
  }

  function escapeHtml(str) {
    if (!str) return "";
    return String(str)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }

  init();
});
