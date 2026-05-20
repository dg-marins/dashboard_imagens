const configForm = document.getElementById("configForm");
const toastStack = document.getElementById("toastStack");
const saveConfigButton = document.getElementById("saveConfig");

let configFields = [];
const collapsedGroups = new Set();

const CONFIG_GROUP_ORDER = [
  "Arquivos",
  "Servidor",
  "Garagens remotas",
  "Banco",
  "Indexacao",
  "Videos",
  "Padroes",
  "Relatorio",
  "Funcionalidades",
];

function showConfigToast(message, kind = "info") {
  if (!toastStack) return;
  const toast = document.createElement("article");
  toast.className = `toast toast-${kind}`;
  const titleByKind = {
    error: "Falha ao salvar",
    success: "Configuracao salva",
    warning: "Reinicializacao necessaria",
    info: "Configuracoes",
  };
  const title = document.createElement("strong");
  title.textContent = titleByKind[kind] || titleByKind.info;
  const content = document.createElement("span");
  content.textContent = message;
  const closeButton = document.createElement("button");
  closeButton.type = "button";
  closeButton.setAttribute("aria-label", "Fechar");
  closeButton.textContent = "x";
  const close = () => toast.remove();
  closeButton.addEventListener("click", close);
  toast.append(title, content, closeButton);
  toastStack.appendChild(toast);
  window.setTimeout(close, kind === "error" || kind === "warning" ? 10000 : 6000);
}

function clearConfigToasts() {
  if (!toastStack) return;
  toastStack.innerHTML = "";
}

function groupFields(fields) {
  const groups = fields.reduce((groups, field) => {
    if (!groups[field.group]) groups[field.group] = [];
    groups[field.group].push(field);
    return groups;
  }, {});

  return Object.entries(groups).sort(([groupA], [groupB]) => {
    const indexA = CONFIG_GROUP_ORDER.indexOf(groupA);
    const indexB = CONFIG_GROUP_ORDER.indexOf(groupB);
    if (indexA === -1 && indexB === -1) return groupA.localeCompare(groupB);
    if (indexA === -1) return 1;
    if (indexB === -1) return -1;
    return indexA - indexB;
  });
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
        <input type="text" value="${escapeHtml(row.name)}" placeholder="Nome" data-remote-name>
      </label>
      <label>
        <span>IP</span>
        <input type="text" value="${escapeHtml(row.ip)}" placeholder="0.0.0.0" data-remote-ip>
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

  if (field.type === "select") {
    const options = field.options || [];
    return `
      <select id="${id}" name="${field.name}">
        ${options.map((option) => `
          <option value="${escapeHtml(option.value)}" ${field.value === option.value ? "selected" : ""}>
            ${escapeHtml(option.label || option.value)}
          </option>
        `).join("")}
      </select>
    `;
  }

  const rows = field.value.length > 80 || field.name === "IMAGE_DASHBOARD_REMOTE_GARAGES" ? 3 : 1;
  if (rows > 1) {
    return `<textarea id="${id}" name="${field.name}" rows="${rows}">${field.value}</textarea>`;
  }

  const type = field.type === "int" ? "number" : (field.type === "password" ? "password" : "text");
  return `<input id="${id}" name="${field.name}" type="${type}" value="${field.value}">`;
}

function fieldDependencyAttrs(field) {
  const dependsOn = field.depends_on || {};
  const entries = Object.entries(dependsOn);
  if (!entries.length) return "";
  const [dependencyName, dependencyValue] = entries[0];
  return ` data-depends-on="${escapeHtml(dependencyName)}" data-depends-value="${escapeHtml(dependencyValue)}"`;
}

function dependencyKey(field) {
  const entries = Object.entries(field.depends_on || {});
  if (!entries.length) return "";
  const [dependencyName, dependencyValue] = entries[0];
  return `${dependencyName}:${dependencyValue}`;
}

function dependencyTitle(field) {
  const entries = Object.entries(field.depends_on || {});
  if (!entries.length) return "";
  const [dependencyName, dependencyValue] = entries[0];
  const dependency = configFields.find((item) => item.name === dependencyName);
  const option = dependency?.options?.find((item) => item.value === dependencyValue);
  return option?.label || dependencyValue;
}

function renderConfigField(field, includeDependencyAttrs = true) {
  return `
    <div class="config-field ${field.name === "IMAGE_DASHBOARD_REMOTE_GARAGES" ? "config-field-wide" : ""}"${includeDependencyAttrs ? fieldDependencyAttrs(field) : ""}>
      <span>${field.label}</span>
      ${inputForField(field)}
      ${field.live ? "" : '<small>Exige reinicio da aplicacao</small>'}
    </div>
  `;
}

function renderFieldGrid(fields, includeDependencyAttrs = true) {
  if (!fields.length) return "";
  return `
    <div class="config-grid">
      ${fields.map((field) => renderConfigField(field, includeDependencyAttrs)).join("")}
    </div>
  `;
}

function renderConfigGroup(groupFieldsList) {
  const baseFields = groupFieldsList.filter((field) => !dependencyKey(field));
  const dependentFields = groupFieldsList.filter((field) => dependencyKey(field));
  const dependentGroups = dependentFields.reduce((groups, field) => {
    const key = dependencyKey(field);
    if (!groups[key]) groups[key] = [];
    groups[key].push(field);
    return groups;
  }, {});

  return `
    ${renderFieldGrid(baseFields)}
    ${Object.values(dependentGroups).map((fields) => {
      const firstField = fields[0];
      return `
        <div class="config-subsection"${fieldDependencyAttrs(firstField)}>
          <h3>${escapeHtml(dependencyTitle(firstField))}</h3>
          ${renderFieldGrid(fields, false)}
        </div>
      `;
    }).join("")}
  `;
}

function toggleConfigPanel(groupName, collapsed) {
  if (collapsed) {
    collapsedGroups.add(groupName);
  } else {
    collapsedGroups.delete(groupName);
  }

  const panel = Array.from(configForm.querySelectorAll("[data-config-group]"))
    .find((item) => item.dataset.configGroup === groupName);
  if (!panel) return;
  panel.classList.toggle("is-collapsed", collapsed);
  const button = panel.querySelector(".config-panel-toggle");
  if (button) {
    button.setAttribute("aria-expanded", String(!collapsed));
    button.setAttribute("aria-label", collapsed ? "Expandir bloco" : "Recolher bloco");
  }
}

function applyFieldVisibility() {
  configForm.querySelectorAll("[data-depends-on]").forEach((fieldBox) => {
    const dependencyName = fieldBox.dataset.dependsOn;
    const expectedValue = fieldBox.dataset.dependsValue;
    const dependency = configForm.elements[dependencyName];
    const currentValue = dependency?.value || "";
    fieldBox.classList.toggle("hidden", currentValue !== expectedValue);
  });
}

function renderConfig(fields) {
  const groups = groupFields(fields);
  configForm.innerHTML = groups.map(([groupName, groupFieldsList]) => {
    const isCollapsed = collapsedGroups.has(groupName);
    return `
    <section class="config-panel ${isCollapsed ? "is-collapsed" : ""}" data-config-group="${escapeHtml(groupName)}">
      <div class="panel-header">
        <div>
          <h2>${groupName}</h2>
        </div>
        <button class="config-panel-toggle" type="button" aria-label="${isCollapsed ? "Expandir bloco" : "Recolher bloco"}" aria-expanded="${String(!isCollapsed)}"></button>
      </div>
      <div class="config-panel-body">
        ${renderConfigGroup(groupFieldsList)}
      </div>
    </section>
  `;
  }).join("");

  document.querySelectorAll("[data-remote-editor]").forEach(setupRemoteGaragesField);
  configForm.onchange = applyFieldVisibility;
  configForm.onclick = (event) => {
    const toggleButton = event.target.closest(".config-panel-toggle");
    if (!toggleButton) return;
    const panel = toggleButton.closest("[data-config-group]");
    if (!panel) return;
    toggleConfigPanel(panel.dataset.configGroup, !panel.classList.contains("is-collapsed"));
  };
  applyFieldVisibility();
}

async function loadConfig() {
  const response = await fetch("/api/config");
  const data = await response.json();
  if (!response.ok || data.status !== "ok") {
    throw new Error(data.message || "Falha ao carregar configuracoes.");
  }
  configFields = data.fields || [];
  renderConfig(configFields);
}

function currentValueForField(field) {
  const element = configForm.elements[field.name];
  if (!element) return null;
  return field.type === "bool" ? (element.checked ? "1" : "0") : element.value;
}

function collectChangedValues() {
  const values = {};
  document.querySelectorAll("[data-remote-editor]").forEach(syncRemoteGaragesValue);
  configFields.forEach((field) => {
    const currentValue = currentValueForField(field);
    if (currentValue === null) return;
    if (String(currentValue) !== String(field.value ?? "")) {
      values[field.name] = currentValue;
    }
  });
  return values;
}

async function saveConfig() {
  saveConfigButton.disabled = true;
  clearConfigToasts();
  showConfigToast("Salvando configuracoes...", "info");
  try {
    const changedValues = collectChangedValues();
    if (!Object.keys(changedValues).length) {
      clearConfigToasts();
      showConfigToast("Nenhuma alteracao para salvar.", "success");
      return;
    }

    const response = await fetch("/api/config", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({values: changedValues}),
    });
    const data = await response.json();
    if (!response.ok || data.status !== "ok") {
      throw new Error(data.message || "Falha ao salvar configuracoes.");
    }

    const restartCount = (data.restart_required || []).length;
    clearConfigToasts();
    showConfigToast(
      restartCount
        ? "Configuracao salva e necessita de reinicializacao."
        : "Configuracao salva.",
      restartCount ? "warning" : "success",
    );
    await loadConfig();
  } catch (error) {
    clearConfigToasts();
    showConfigToast(error.message || "Falha ao salvar.", "error");
  } finally {
    saveConfigButton.disabled = false;
  }
}

saveConfigButton.addEventListener("click", saveConfig);

loadConfig().catch((error) => {
  showConfigToast(error.message || "Falha ao carregar configuracoes.", "error");
});
