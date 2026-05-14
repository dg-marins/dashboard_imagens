const configForm = document.getElementById("configForm");
const configFile = document.getElementById("configFile");
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

function inputForField(field) {
  const id = `config_${field.name}`;
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
          <p class="panel-label">Grupo</p>
          <h2>${groupName}</h2>
        </div>
      </div>
      <div class="config-grid">
        ${groupFieldsList.map((field) => `
          <label class="config-field">
            <span>${field.label}</span>
            ${inputForField(field)}
            <small>${field.name}${field.live ? "" : " - exige reinicio"}</small>
          </label>
        `).join("")}
      </div>
    </section>
  `).join("");
}

async function loadConfig() {
  hideConfigStatus();
  const response = await fetch("/api/config");
  const data = await response.json();
  if (!response.ok || data.status !== "ok") {
    throw new Error(data.message || "Falha ao carregar configuracoes.");
  }
  configFields = data.fields || [];
  configFile.textContent = data.config_file || "-";
  renderConfig(configFields);
}

function collectValues() {
  const values = {};
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
