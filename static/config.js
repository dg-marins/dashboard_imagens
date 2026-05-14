const configForm = document.getElementById("configForm");
const configStatus = document.getElementById("configStatus");
const saveConfigButton = document.getElementById("saveConfig");

let configFields = [];

function showConfigStatus(message, kind = "info") {
  configStatus.textContent = message;
  configStatus.className = `status-message config-status ${kind}`;
}

function hideConfigStatus() {
  configStatus.textContent = "";
  configStatus.className = "status-message hidden";
}

function groupFields(fields) {
  return fields.reduce((groups, field) => {
    if (!groups[field.group]) groups[field.group] = [];
    groups[field.group].push(field);
    return groups;
  }, {});
}

function escapeHtml(value) {
  return String(value || "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function parseRemoteGarages(value) {
  return String(value || "")
    .split(";")
    .map((item) => item.trim())
    .filter(Boolean)
    .map((item) => {
      const separatorIndex = item.indexOf(":");
      if (separatorIndex === -1) {
        return {name: "", ip: item.replace(/^https?:\/\//, ""), port: ""};
      }

      const name = item.slice(0, separatorIndex).trim();
      const rawUrl = item.slice(separatorIndex + 1).trim();
      let urlText = rawUrl;
      if (!/^https?:\/\//i.test(urlText)) {
        urlText = `http://${urlText}`;
      }

      try {
        const url = new URL(urlText);
        return {
          name,
          ip: url.hostname,
          port: url.port || (url.protocol === "https:" ? "443" : "80"),
        };
      } catch (_) {
        const [ip, port = ""] = rawUrl.replace(/^https?:\/\//, "").split(":");
        return {name, ip, port};
      }
    });
}

function remoteGaragesToConfig(rows) {
  return rows
    .map((row) => {
      const name = row.querySelector("[data-remote-name]")?.value.trim();
      const ip = row.querySelector("[data-remote-ip]")?.value.trim();
      const port = row.querySelector("[data-remote-port]")?.value.trim();
      if (!name || !ip) return "";
      const host = port ? `${ip}:${port}` : ip;
      return `${name}:http://${host}`;
    })
    .filter(Boolean)
    .join(";");
}

function syncRemoteGaragesValue(wrapper) {
  const hidden = wrapper.querySelector('input[type="hidden"]');
  hidden.value = remoteGaragesToConfig(Array.from(wrapper.querySelectorAll(".remote-garage-row")));
}

function remoteGarageRow(row = {}) {
  return `
    <div class="remote-garage-row">
      <label>
        <span>Nome</span>
        <input type="text" value="${escapeHtml(row.name)}" placeholder="G2" data-remote-name>
      </label>
      <label>
        <span>IP</span>
        <input type="text" value="${escapeHtml(row.ip)}" placeholder="10.90.0.14" data-remote-ip>
      </label>
      <label>
        <span>Porta</span>
        <input type="number" value="${escapeHtml(row.port)}" placeholder="8081" min="1" max="65535" data-remote-port>
      </label>
      <button type="button" class="remote-remove" title="Remover garagem">Remover</button>
    </div>
  `;
}

function setupRemoteGaragesField(wrapper) {
  const rowsBox = wrapper.querySelector(".remote-garage-rows");
  const addButton = wrapper.querySelector("[data-add-remote]");

  function addRow(row = {}) {
    rowsBox.insertAdjacentHTML("beforeend", remoteGarageRow(row));
    syncRemoteGaragesValue(wrapper);
  }

  addButton.addEventListener("click", () => addRow({port: "8081"}));
  rowsBox.addEventListener("input", () => syncRemoteGaragesValue(wrapper));
  rowsBox.addEventListener("click", (event) => {
    const removeButton = event.target.closest(".remote-remove");
    if (!removeButton) return;
    removeButton.closest(".remote-garage-row").remove();
    syncRemoteGaragesValue(wrapper);
  });
}

function inputForField(field) {
  const id = `config_${field.name}`;
  if (field.name === "IMAGE_DASHBOARD_REMOTE_GARAGES") {
    const rows = parseRemoteGarages(field.value);
    return `
      <div class="remote-garages-editor" data-remote-editor>
        <input id="${id}" name="${field.name}" type="hidden" value="${escapeHtml(field.value)}">
        <div class="remote-garage-rows">
          ${(rows.length ? rows : [{port: "8081"}]).map(remoteGarageRow).join("")}
        </div>
        <button type="button" class="remote-add" data-add-remote>Adicionar garagem</button>
      </div>
    `;
  }

  if (field.type === "bool") {
    return `
      <label class="config-toggle" for="${id}">
        <input id="${id}" name="${field.name}" type="checkbox" ${field.value === "1" ? "checked" : ""}>
        <span>Ativo</span>
      </label>
    `;
  }

  const rows = field.value.length > 80 || field.name === "IMAGE_DASHBOARD_REMOTE_GARAGES" ? 3 : 1;
  if (rows > 1) {
    return `<textarea id="${id}" name="${field.name}" rows="${rows}">${field.value}</textarea>`;
  }

  const type = field.type === "int" ? "number" : "text";
  return `<input id="${id}" name="${field.name}" type="${type}" value="${field.value}">`;
}

function renderConfig(fields) {
  const groups = groupFields(fields);
  configForm.innerHTML = Object.entries(groups).map(([groupName, groupFieldsList]) => `
    <section class="config-panel">
      <div class="panel-header">
        <div>
          <h2>${groupName}</h2>
        </div>
      </div>
      <div class="config-grid">
        ${groupFieldsList.map((field) => `
          <div class="config-field ${field.name === "IMAGE_DASHBOARD_REMOTE_GARAGES" ? "config-field-wide" : ""}">
            <span>${field.label}</span>
            ${inputForField(field)}
            ${field.live ? "" : '<small>Exige reinicio da aplicacao</small>'}
          </div>
        `).join("")}
      </div>
    </section>
  `).join("");

  document.querySelectorAll("[data-remote-editor]").forEach(setupRemoteGaragesField);
}

async function loadConfig() {
  hideConfigStatus();
  const response = await fetch("/api/config");
  const data = await response.json();
  if (!response.ok || data.status !== "ok") {
    throw new Error(data.message || "Falha ao carregar configuracoes.");
  }
  configFields = data.fields || [];
  renderConfig(configFields);
}

function collectValues() {
  const values = {};
  document.querySelectorAll("[data-remote-editor]").forEach(syncRemoteGaragesValue);
  configFields.forEach((field) => {
    const element = configForm.elements[field.name];
    if (!element) return;
    values[field.name] = field.type === "bool" ? (element.checked ? "1" : "0") : element.value;
  });
  return values;
}

async function saveConfig() {
  saveConfigButton.disabled = true;
  showConfigStatus("Salvando configuracoes...");
  try {
    const response = await fetch("/api/config", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({values: collectValues()}),
    });
    const data = await response.json();
    if (!response.ok || data.status !== "ok") {
      throw new Error(data.message || "Falha ao salvar configuracoes.");
    }

    const restartCount = (data.restart_required || []).length;
    const suffix = restartCount
      ? ` ${restartCount} item(ns) foram salvos, mas so entram em vigor apos reiniciar.`
      : "";
    showConfigStatus(`Configuracoes salvas.${suffix}`, restartCount ? "warning" : "success");
    await loadConfig();
  } catch (error) {
    showConfigStatus(error.message || "Falha ao salvar configuracoes.", "error");
  } finally {
    saveConfigButton.disabled = false;
  }
}

saveConfigButton.addEventListener("click", saveConfig);

loadConfig().catch((error) => {
  showConfigStatus(error.message || "Falha ao carregar configuracoes.", "error");
});
