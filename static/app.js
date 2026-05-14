const monthSelect = document.getElementById("month");
const yearSelect = document.getElementById("year");
const garageSelect = document.getElementById("garage");
const vehicleSelect = document.getElementById("vehicle");
const cameraSelect = document.getElementById("camera");
const garagePicker = document.getElementById("garagePicker");
const vehiclePicker = document.getElementById("vehiclePicker");
const cameraPicker = document.getElementById("cameraPicker");
const resetButton = document.getElementById("resetFilters");
const filtersBar = document.querySelector(".filters-bar");

const summaryGrid = document.getElementById("summaryGrid");
const garageStatusGrid = document.getElementById("garageStatusGrid");
const dailyOverview = document.getElementById("dailyOverview");
const topRows = document.getElementById("topRows");
const matrixTable = document.getElementById("matrixTable");
const statusMessage = document.getElementById("statusMessage");
const summaryCardTemplate = document.getElementById("summaryCardTemplate");

const monthNames = [
  "Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho",
  "Julho", "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro"
];

let scanPollTimer = null;
let dashboardRequestId = 0;
let dashboardRefreshTimer = null;
const DASHBOARD_REFRESH_INTERVAL_MS = 30000;

function updateStickyOffsets() {
  if (!filtersBar) return;
  document.documentElement.style.setProperty(
    "--filters-height",
    `${Math.ceil(filtersBar.getBoundingClientRect().height)}px`
  );
}

function createMultiPicker(root, select, placeholder) {
  root.innerHTML = `
    <button class="multi-picker-button" type="button" aria-expanded="false">
      <span>${placeholder}</span>
      <b>v</b>
    </button>
    <div class="multi-picker-menu" hidden>
      <input class="multi-picker-search" type="search" placeholder="Pesquisar...">
      <div class="multi-picker-options"></div>
    </div>
  `;

  const button = root.querySelector(".multi-picker-button");
  const buttonText = button.querySelector("span");
  const menu = root.querySelector(".multi-picker-menu");
  const search = root.querySelector(".multi-picker-search");
  const optionsBox = root.querySelector(".multi-picker-options");
  let dirty = false;

  root.addEventListener("click", (event) => {
    event.stopPropagation();
  });

  function updateButton() {
    const selected = selectedValues(select);
    if (!selected.length) {
      buttonText.textContent = placeholder;
    } else if (selected.length === 1) {
      buttonText.textContent = selected[0];
    } else {
      buttonText.textContent = `${selected.length} selecionados`;
    }
  }

  function renderOptions() {
    const term = search.value.trim().toLowerCase();
    const selected = new Set(selectedValues(select));
    const options = Array.from(select.options)
      .filter((option) => option.value.toLowerCase().includes(term));

    optionsBox.innerHTML = "";
    if (!options.length) {
      optionsBox.innerHTML = '<p class="multi-picker-empty">Nenhuma opção</p>';
      return;
    }

    options.forEach((option) => {
      const item = document.createElement("button");
      item.type = "button";
      item.className = "multi-picker-option";
      item.setAttribute("aria-pressed", selected.has(option.value) ? "true" : "false");
      item.innerHTML = `
        <span class="multi-picker-check">${selected.has(option.value) ? "x" : ""}</span>
        <span>${option.value}</span>
      `;
      item.addEventListener("click", (event) => {
        event.preventDefault();
        event.stopPropagation();
        option.selected = !option.selected;
        dirty = true;
        updateButton();
        renderOptions();
      });
      optionsBox.appendChild(item);
    });
  }

  function close(applyChanges = true) {
    const shouldApply = applyChanges && dirty;
    menu.hidden = true;
    button.setAttribute("aria-expanded", "false");
    dirty = false;
    if (shouldApply) {
      loadDashboard();
    }
  }

  function open() {
    document.querySelectorAll(".multi-picker-menu").forEach((otherMenu) => {
      if (otherMenu !== menu) otherMenu.hidden = true;
    });
    menu.hidden = false;
    button.setAttribute("aria-expanded", "true");
    search.value = "";
    renderOptions();
    search.focus();
  }

  button.addEventListener("click", () => {
    if (!menu.hidden) {
      close();
      return;
    }

    open();
  });
  search.addEventListener("input", renderOptions);

  function isOpen() {
    return !menu.hidden;
  }

  function reopen() {
    menu.hidden = false;
    button.setAttribute("aria-expanded", "true");
    renderOptions();
  }

  return { renderOptions, updateButton, close, isOpen, reopen };
}

const garagePickerApi = createMultiPicker(garagePicker, garageSelect, "Todas as garagens");
const vehiclePickerApi = createMultiPicker(vehiclePicker, vehicleSelect, "Todos os veículos");
const cameraPickerApi = createMultiPicker(cameraPicker, cameraSelect, "Todas as câmeras");

document.addEventListener("click", (event) => {
  if (!garagePicker.contains(event.target)) garagePickerApi.close();
  if (!vehiclePicker.contains(event.target)) vehiclePickerApi.close();
  if (!cameraPicker.contains(event.target)) cameraPickerApi.close();
});

function fillDateFilters() {
  const now = new Date();
  monthNames.forEach((name, index) => {
    const option = document.createElement("option");
    option.value = index + 1;
    option.textContent = name;
    if (index === now.getMonth()) option.selected = true;
    monthSelect.appendChild(option);
  });

  for (let year = now.getFullYear() - 2; year <= now.getFullYear() + 1; year += 1) {
    const option = document.createElement("option");
    option.value = year;
    option.textContent = year;
    if (year === now.getFullYear()) option.selected = true;
    yearSelect.appendChild(option);
  }
}

function selectedValues(select) {
  return Array.from(select.selectedOptions).map((option) => option.value);
}

function clearSelection(select) {
  Array.from(select.options).forEach((option) => {
    option.selected = false;
  });
}

function selectValues(select, values) {
  const selected = new Set(values || []);
  Array.from(select.options).forEach((option) => {
    option.selected = selected.has(option.value);
  });
}

function populateMultiSelect(select, values) {
  const selected = new Set(selectedValues(select));
  const nextValues = values || [];
  const currentValues = Array.from(select.options).map((option) => option.value);

  if (
    currentValues.length === nextValues.length &&
    currentValues.every((value, index) => value === nextValues[index])
  ) {
    return;
  }

  select.innerHTML = "";
  nextValues.forEach((value) => {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = value;
    option.selected = selected.has(value);
    select.appendChild(option);
  });
}

function syncPickers() {
  garagePickerApi.updateButton();
  garagePickerApi.renderOptions();
  vehiclePickerApi.updateButton();
  vehiclePickerApi.renderOptions();
  cameraPickerApi.updateButton();
  cameraPickerApi.renderOptions();
}

function formatNumber(value) {
  return new Intl.NumberFormat("pt-BR").format(value || 0);
}

function formatDate(value) {
  if (!value || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return value || "";
  const [year, month, day] = value.split("-");
  return `${day}-${month}-${year}`;
}

function formatSlashDate(value) {
  if (!value || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return value || "";
  const [year, month, day] = value.split("-");
  return `${day}/${month}/${year}`;
}

function formatBytes(value) {
  const bytes = Number(value || 0);
  if (bytes <= 0) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const index = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
  const amount = bytes / (1024 ** index);
  const decimals = 1;
  return `${new Intl.NumberFormat("pt-BR", {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  }).format(amount)} ${units[index]}`;
}

function formatTimestamp(value) {
  if (!value) return "Sem captura";
  return formatDate(value);
}

function formatConnectionTimestamp(value) {
  if (!value) return null;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  const time = date.toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" });
  const day = date.toLocaleDateString("pt-BR");
  return `${time}<br>${day}`;
}

function formatMatrixHeaderDate(value) {
  if (!value || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return value || "";
  const [year, month, day] = value.split("-");
  return `${day}/${month}/${year.slice(2)}`;
}

function currentMatrixDates() {
  const month = Number(monthSelect.value || new Date().getMonth() + 1);
  const year = Number(yearSelect.value || new Date().getFullYear());
  const today = new Date();
  const lastDay = new Date(year, month, 0).getDate();
  const isCurrentMonth = year === today.getFullYear() && month === today.getMonth() + 1;
  const endDay = isCurrentMonth ? today.getDate() : lastDay;
  const dates = [];

  for (let day = endDay; day >= 1; day -= 1) {
    dates.push(`${year}-${String(month).padStart(2, "0")}-${String(day).padStart(2, "0")}`);
  }

  return dates;
}

function setSectionLoading(isLoading) {
  [summaryGrid, dailyOverview, topRows, matrixTable].forEach((element) => {
    if (!element) return;
    element.classList.toggle("is-loading", isLoading);
  });
}

function snapshotUpdateValues() {
  const snapshot = new Map();
  document.querySelectorAll("[data-update-key]").forEach((element) => {
    snapshot.set(element.dataset.updateKey, element.textContent.trim());
  });
  return snapshot;
}

function markChangedValues(previousValues) {
  document.querySelectorAll("[data-update-key]").forEach((element) => {
    const previousValue = previousValues.get(element.dataset.updateKey);
    const currentValue = element.textContent.trim();
    if (previousValue !== undefined && previousValue !== currentValue) {
      element.classList.remove("value-updated");
      void element.offsetWidth;
      element.classList.add("value-updated");
      element.setAttribute("aria-label", currentValue);
      window.setTimeout(() => {
        element.classList.remove("value-updated");
      }, 650);
    }
  });
}

function animateSectionUpdate() {
  [summaryGrid, dailyOverview, topRows, matrixTable].forEach((element) => {
    if (!element) return;
    element.classList.remove("data-ready");
    void element.offsetWidth;
    element.classList.add("data-ready");
  });
}

function renderLoadingSummary() {
  summaryGrid.innerHTML = `
    <article class="summary-card skeleton-card">
      <p class="skeleton-line w-50"></p>
      <strong class="skeleton-block"></strong>
      <span class="skeleton-line w-80"></span>
    </article>
  `;
}

function renderLoadingDaily() {
  dailyOverview.innerHTML = Array.from({length: 7}).map(() => `
    <article class="day-card skeleton-card">
      <span class="skeleton-line w-60"></span>
      <strong class="skeleton-block small"></strong>
      <small class="skeleton-line w-70"></small>
    </article>
  `).join("");
}

function renderLoadingTopRows() {
  topRows.innerHTML = Array.from({length: 4}).map(() => `
    <article class="top-card skeleton-card">
      <span class="skeleton-line w-70"></span>
      <strong class="skeleton-block small"></strong>
      <small class="skeleton-line w-50"></small>
      <em class="skeleton-line w-90"></em>
    </article>
  `).join("");
}

function renderLoadingMatrix(dates = currentMatrixDates()) {
  const thead = matrixTable.querySelector("thead");
  const tbody = matrixTable.querySelector("tbody");

  thead.innerHTML = "";
  tbody.innerHTML = "";

  const headRow = document.createElement("tr");
  headRow.innerHTML = `
    <th class="sticky vehicle-column" style="min-width:150px">Frota</th>
    <th class="sticky-2" style="min-width:220px">Câmeras</th>
    <th>Dias</th>
    ${dates.map((date) => `<th>${formatMatrixHeaderDate(date)}</th>`).join("")}
  `;
  thead.appendChild(headRow);

  Array.from({length: 8}).forEach((_, index) => {
    const row = document.createElement("tr");
    row.innerHTML = `
      <td class="sticky"><span class="skeleton-line w-60"></span></td>
      <td class="sticky-2"><span class="skeleton-line w-80"></span></td>
      <td><span class="skeleton-line w-30 center"></span></td>
      ${dates.map(() => `
        <td class="${index % 3 === 0 ? "cell-medium" : "cell-none"} skeleton-cell">
          <span class="skeleton-line w-50 center"></span>
          <span class="skeleton-line w-70 center"></span>
        </td>
      `).join("")}
    `;
    tbody.appendChild(row);
  });
}

function renderLoadingDashboard() {
  hideStatus();
  renderLoadingSummary();
  renderLoadingDaily();
  renderLoadingTopRows();
  renderLoadingMatrix();
  setSectionLoading(true);
}

function showStatus(message) {
  statusMessage.textContent = message;
  statusMessage.classList.remove("hidden");
}

function hideStatus() {
  statusMessage.textContent = "";
  statusMessage.classList.add("hidden");
}

function buildQuery() {
  const params = new URLSearchParams();
  params.set("month", monthSelect.value);
  params.set("year", yearSelect.value);
  selectedValues(garageSelect).forEach((garage) => params.append("garage", garage));
  selectedValues(vehicleSelect).forEach((vehicle) => params.append("vehicle", vehicle));
  selectedValues(cameraSelect).forEach((camera) => params.append("camera", camera));
  return params.toString();
}

function renderSummary(summary) {
  summaryGrid.innerHTML = "";

  const alertVehicles = summary.alert_vehicles || [];
  const node = summaryCardTemplate.content.cloneNode(true);
  const card = node.querySelector(".summary-card");
  card.classList.add("alert-summary-card");
  card.setAttribute("role", "button");
  card.setAttribute("tabindex", "0");
  card.querySelector(".summary-title").textContent = "Veículos em alerta";
  const summaryValue = card.querySelector(".summary-value");
  summaryValue.dataset.updateKey = "summary-alert-vehicles";
  summaryValue.textContent = formatNumber(summary.alert_vehicle_count);
  card.querySelector(".summary-note").textContent = alertVehicles.length
    ? "Clique para filtrar veículos há 3 dias ou mais sem imagens."
    : "Nenhum veículo há 3 dias ou mais sem imagens.";

  const applyAlertFilter = () => {
    if (!alertVehicles.length) return;
    selectValues(vehicleSelect, alertVehicles);
    syncPickers();
    loadDashboard();
  };

  card.addEventListener("click", applyAlertFilter);
  card.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      applyAlertFilter();
    }
  });
  summaryGrid.appendChild(node);
}

function renderGarageStatus(statuses) {
  if (!garageStatusGrid) return;
  garageStatusGrid.innerHTML = "";

  (statuses || []).forEach((garage) => {
    const labelByStatus = {
      online: "Online",
      offline: "Offline",
    };
    const statusLabel = labelByStatus[garage.status] || garage.status || "Offline";
    const syncDetail = garage.syncing
      ? `Sincronizando: etapa ${garage.step || "-"}${garage.imported_files ? `, ${garage.imported_files.toLocaleString("pt-BR")} arquivos` : ""}`
      : "";
    const lastConnection = formatConnectionTimestamp(garage.last_online_at);
    const offlineDetail = garage.status === "offline"
      ? (lastConnection ? `Ultima conexao:<br>${lastConnection}` : "Aguardando Primeira Conexao")
      : "";
    const article = document.createElement("article");
    article.className = `garage-status-card ${garage.status || "offline"}${garage.syncing ? " syncing" : ""}`;
    if (syncDetail) {
      article.title = syncDetail;
    } else if (offlineDetail) {
      article.title = offlineDetail.replaceAll("<br>", "\n");
    }
    article.innerHTML = `
      <span>${garage.name}</span>
      <strong>${statusLabel}</strong>
      ${offlineDetail ? `
        <span class="sync-tooltip">
          ${offlineDetail}
        </span>
      ` : ""}
      ${garage.syncing ? `
        <span class="sync-tooltip">
          <i class="mini-loader" aria-hidden="true"></i>
          ${syncDetail}
        </span>
      ` : ""}
    `;
    garageStatusGrid.appendChild(article);
  });
}

async function fetchScanStatus() {
  const response = await fetch("/api/scan-status");
  return response.json();
}

async function syncScanStatus() {
  try {
    const scan = await fetchScanStatus();
    if (scan.running) {
      startScanPolling();
      return scan;
    }

    stopScanPolling();
    return scan;
  } catch (_) {
    return null;
  }
}

function startScanPolling() {
  if (scanPollTimer) return;
  scanPollTimer = window.setInterval(async () => {
    const scan = await syncScanStatus();
    if (scan && !scan.running) {
      await loadDashboard();
    }
  }, 2500);
}

function stopScanPolling() {
  if (!scanPollTimer) return;
  window.clearInterval(scanPollTimer);
  scanPollTimer = null;
}

function renderDaily(days) {
  const lastSevenDays = days.slice(0, 7);
  dailyOverview.innerHTML = "";

  if (!lastSevenDays.length) {
    dailyOverview.innerHTML = '<p class="muted">Nenhum arquivo encontrado para os filtros atuais.</p>';
    return;
  }

  lastSevenDays.forEach((day) => {
    const article = document.createElement("article");
    article.className = "day-card";
    article.innerHTML = `
      <dl class="daily-metrics">
        <div>
          <dt>Data:</dt>
          <dd>${formatSlashDate(day.date)}</dd>
        </div>
        <div>
          <dt>Veiculos:</dt>
          <dd data-update-key="daily-${day.date}-vehicles">${formatNumber(day.vehicles)}</dd>
        </div>
        <div>
          <dt>Volume:</dt>
          <dd data-update-key="daily-${day.date}-volume">${formatBytes(day.total_size_bytes)}</dd>
        </div>
        <div>
          <dt>Arquivos:</dt>
          <dd data-update-key="daily-${day.date}-total">${formatNumber(day.total)}</dd>
        </div>
      </dl>
    `;
    dailyOverview.appendChild(article);
  });
}

function renderTopRows(rows) {
  topRows.innerHTML = "";
  if (!rows.length) {
    topRows.innerHTML = '<p class="muted">Sem destaques para o período atual.</p>';
    return;
  }

  rows.forEach((row) => {
    const article = document.createElement("article");
    article.className = "top-card";
    const topKey = `top-${row.vehicle}-${row.camera}`;
    article.innerHTML = `
      <span>${row.vehicle} / ${row.camera}</span>
      <strong data-update-key="${topKey}-total">${formatNumber(row.total)}</strong>
      <small data-update-key="${topKey}-days">${formatNumber(row.active_days)} dias ativos</small>
      <em>Último: ${formatTimestamp(row.latest_file)}</em>
    `;
    topRows.appendChild(article);
  });
}

function renderMatrix(dates, rows, fleetTotal = 0) {
  const thead = matrixTable.querySelector("thead");
  const tbody = matrixTable.querySelector("tbody");

  thead.innerHTML = "";
  tbody.innerHTML = "";

  const headRow = document.createElement("tr");
  headRow.innerHTML = `
    <th class="sticky vehicle-column" style="min-width:150px">Frota: ${formatNumber(fleetTotal)}</th>
    <th class="sticky-2" style="min-width:220px">Câmeras</th>
    <th>Dias</th>
    ${dates.map((date) => `<th>${formatMatrixHeaderDate(date)}</th>`).join("")}
  `;
  thead.appendChild(headRow);

  if (!rows.length) {
    const row = document.createElement("tr");
    row.innerHTML = `<td colspan="${dates.length + 3}" class="muted">Nenhum registro encontrado.</td>`;
    tbody.appendChild(row);
    return;
  }

  rows.forEach((entry) => {
    const tr = document.createElement("tr");
    const dayCells = dates.map((date) => {
      const day = entry.days[date];
      if (!day) return '<td class="cell-none">0</td>';
      const cellKey = `matrix-${entry.vehicle}-${date}`;

      const garageNames = day.garage_names || [];
      const garageBadges = garageNames.length
        ? `<div class="garage-badge-stack">${garageNames.map((garage) => `<span class="garage-badge">${garage}</span>`).join("")}</div>`
        : "";

      const cameraDetails = day.cameras
        .map((camera) => `<span class="camera-chip" data-update-key="${cellKey}-${camera.name}">${camera.name}: ${formatNumber(camera.count)}</span>`)
        .join("");

      return `
        <td class="cell-${day.level}">
          ${garageBadges}
          <div class="day-total" data-update-key="${cellKey}-total">${formatNumber(day.count)}</div>
          <div class="camera-stack">${cameraDetails}</div>
        </td>
      `;
    }).join("");

    const cameras = entry.cameras
      .map((camera) => `<span class="camera-chip">${camera}</span>`)
      .join("");

    tr.innerHTML = `
      <td class="sticky vehicle-column">${entry.vehicle}</td>
      <td class="sticky-2"><div class="camera-stack camera-grid">${cameras}</div></td>
      <td data-update-key="row-${entry.vehicle}-days">${formatNumber(entry.active_days)}</td>
      ${dayCells}
    `;
    tbody.appendChild(tr);
  });
}

function renderFilterOptions(filters) {
  if (!filters) return;
  const keepGarageOpen = garagePickerApi.isOpen();
  const keepVehicleOpen = vehiclePickerApi.isOpen();
  const keepCameraOpen = cameraPickerApi.isOpen();
  populateMultiSelect(garageSelect, filters.garages);
  populateMultiSelect(vehicleSelect, filters.vehicles);
  populateMultiSelect(cameraSelect, filters.cameras);
  syncPickers();
  if (keepGarageOpen) garagePickerApi.reopen();
  if (keepVehicleOpen) vehiclePickerApi.reopen();
  if (keepCameraOpen) cameraPickerApi.reopen();
}

async function loadDashboard(options = {}) {
  const { showLoading = true, animateAll = true, animateChanges = false } = options;
  const requestId = ++dashboardRequestId;
  const previousValues = animateChanges ? snapshotUpdateValues() : null;
  hideStatus();
  if (showLoading) {
    renderLoadingDashboard();
  }

  try {
    const response = await fetch(`/api/dashboard?${buildQuery()}`);
    const data = await response.json();

    if (requestId !== dashboardRequestId) {
      return;
    }

    if (!response.ok) {
      throw new Error(data.message || "A API retornou erro ao montar o relatório.");
    }

    renderFilterOptions(data.available_filters);
    renderSummary(data.summary);
    renderGarageStatus(data.garage_status);
    renderDaily(data.daily_overview);
    renderTopRows(data.top_rows);
    renderMatrix(data.dates, data.rows, data.summary.fleet_total);
    setSectionLoading(false);
    if (animateChanges && previousValues) {
      markChangedValues(previousValues);
    } else if (animateAll) {
      animateSectionUpdate();
    }

    if (data.scan_info && data.scan_info.current_status && data.scan_info.current_status.running) {
      startScanPolling();
    }
  } catch (error) {
    if (requestId !== dashboardRequestId) {
      return;
    }
    setSectionLoading(false);
    showStatus(`Falha ao carregar dados: ${error.message}`);
    if (!showLoading) {
      return;
    }
    summaryGrid.innerHTML = '<p class="muted">Não foi possível carregar o relatório.</p>';
    garageStatusGrid.innerHTML = "";
    dailyOverview.innerHTML = "";
    topRows.innerHTML = "";
    matrixTable.querySelector("thead").innerHTML = "";
    matrixTable.querySelector("tbody").innerHTML = "";
  }
}

function startDashboardAutoRefresh() {
  if (dashboardRefreshTimer) return;
  dashboardRefreshTimer = window.setInterval(() => {
    if (document.hidden) return;
    loadDashboard({ showLoading: false, animateAll: false, animateChanges: true });
  }, DASHBOARD_REFRESH_INTERVAL_MS);
}

function stopDashboardAutoRefresh() {
  if (!dashboardRefreshTimer) return;
  window.clearInterval(dashboardRefreshTimer);
  dashboardRefreshTimer = null;
}

function resetFilters() {
  clearSelection(garageSelect);
  clearSelection(vehicleSelect);
  clearSelection(cameraSelect);
  syncPickers();
  const now = new Date();
  monthSelect.value = String(now.getMonth() + 1);
  yearSelect.value = String(now.getFullYear());
  loadDashboard();
}

monthSelect.addEventListener("change", loadDashboard);
yearSelect.addEventListener("change", loadDashboard);
resetButton.addEventListener("click", resetFilters);

window.addEventListener("beforeunload", () => {
  stopScanPolling();
  stopDashboardAutoRefresh();
});

window.addEventListener("resize", updateStickyOffsets);
if (window.ResizeObserver && filtersBar) {
  new ResizeObserver(updateStickyOffsets).observe(filtersBar);
}

updateStickyOffsets();
fillDateFilters();
syncScanStatus();
loadDashboard();
startDashboardAutoRefresh();
