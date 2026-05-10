// UACJ OBD-II Simulator — UI strings (English / Español)
//
// Keys are short identifiers used by the page templates via data-i18n
// attributes. To add a string:
//   1. Add the key + en/es translation here
//   2. Mark the HTML element: <h2 data-i18n="dashboard.dtcs">DTCs</h2>
// The applyI18n() helper reads the active language (localStorage:
// `uacj_lang`, default `en`) and rewrites text content + placeholders.

(function () {
  const STRINGS = {
    en: {
      "app.title": "UACJ OBD-II Training Simulator",
      "app.subtitle.acquisition": "Phase 1 — Acquisition Dashboard",
      "app.subtitle.scenarios": "Scenarios",
      "app.subtitle.classroom": "Classroom view",
      "app.subtitle.session": "Session detail",
      "app.subtitle.diff": "Session diff",
      "nav.scenarios": "Scenarios →",
      "nav.classroom": "Classroom →",
      "nav.diff": "Diff →",
      "nav.dashboard": "← Dashboard",
      "lang.toggle": "ES",
      "session.heading": "Session",
      "session.adapter": "Adapter",
      "session.port": "Port (e.g. /dev/ttyUSB0)",
      "session.start": "Start",
      "session.stop": "Stop",
      "session.no_vehicle": "no vehicle connected",
      "vehicles.heading": "Vehicles",
      "vehicles.none": "none yet",
      "sessions.heading": "Past Sessions",
      "sessions.none": "none yet",
      "live.heading": "Live Parameters",
      "dtcs.heading": "DTCs",
      "dtcs.code": "Code",
      "dtcs.status": "Status",
      "dtcs.description": "Description",
      "dtcs.none": "no DTCs",
      "monitors.heading": "Readiness Monitors",
      "monitors.name": "Monitor",
      "monitors.supported": "Supported",
      "monitors.ready": "Ready",
      "monitors.yes": "yes",
      "monitors.no": "no",
      "monitors.ready_yes": "ready",
      "monitors.ready_no": "incomplete",
      "status.idle": "idle",
      "status.starting": "starting…",
      "status.recording": "recording",
      "status.stopping": "stopping…",
      "status.error": "error",
      "scenarios.heading": "Scenarios",
      "scenarios.new_from_preset": "New from preset",
      "scenarios.create": "Create",
      "scenarios.push": "Push to simulator",
      "scenarios.replay": "Replay self-test",
      "scenarios.delete": "Delete",
      "scenarios.label": "Label",
      "scenarios.source_session": "Source session",
      "scenarios.dtcs": "DTCs",
      "scenarios.live_overrides": "Live overrides",
      "classroom.heading": "Classroom view",
      "classroom.live_log": "Live request log (auto-refresh)",
      "classroom.no_requests": "no requests yet",
      "diff.heading": "Compare two sessions",
      "diff.session_a": "Session A",
      "diff.session_b": "Session B",
      "diff.compare": "Compare",
      "diff.added": "added",
      "diff.removed": "removed",
      "diff.shifted": "shifted",
      "tooltip.lang": "Cambiar idioma",
      "common.backup": "Backup all data",
      "common.restore": "Restore from backup",
      "common.export_csv": "Export CSV",
      "common.export_json": "Export JSON",
    },
    es: {
      "app.title": "Simulador OBD-II UACJ para Capacitación",
      "app.subtitle.acquisition": "Fase 1 — Panel de Adquisición",
      "app.subtitle.scenarios": "Escenarios",
      "app.subtitle.classroom": "Vista de Aula",
      "app.subtitle.session": "Detalle de Sesión",
      "app.subtitle.diff": "Comparar Sesiones",
      "nav.scenarios": "Escenarios →",
      "nav.classroom": "Aula →",
      "nav.diff": "Comparar →",
      "nav.dashboard": "← Panel",
      "lang.toggle": "EN",
      "session.heading": "Sesión",
      "session.adapter": "Adaptador",
      "session.port": "Puerto (ej. /dev/ttyUSB0)",
      "session.start": "Iniciar",
      "session.stop": "Detener",
      "session.no_vehicle": "ningún vehículo conectado",
      "vehicles.heading": "Vehículos",
      "vehicles.none": "ninguno aún",
      "sessions.heading": "Sesiones Anteriores",
      "sessions.none": "ninguna aún",
      "live.heading": "Parámetros en Vivo",
      "dtcs.heading": "Códigos DTC",
      "dtcs.code": "Código",
      "dtcs.status": "Estado",
      "dtcs.description": "Descripción",
      "dtcs.none": "sin códigos DTC",
      "monitors.heading": "Monitores de Disponibilidad",
      "monitors.name": "Monitor",
      "monitors.supported": "Soportado",
      "monitors.ready": "Listo",
      "monitors.yes": "sí",
      "monitors.no": "no",
      "monitors.ready_yes": "listo",
      "monitors.ready_no": "incompleto",
      "status.idle": "inactivo",
      "status.starting": "iniciando…",
      "status.recording": "grabando",
      "status.stopping": "deteniendo…",
      "status.error": "error",
      "scenarios.heading": "Escenarios",
      "scenarios.new_from_preset": "Nuevo desde plantilla",
      "scenarios.create": "Crear",
      "scenarios.push": "Enviar al simulador",
      "scenarios.replay": "Auto-prueba de reproducción",
      "scenarios.delete": "Eliminar",
      "scenarios.label": "Nombre",
      "scenarios.source_session": "Sesión origen",
      "scenarios.dtcs": "Códigos DTC",
      "scenarios.live_overrides": "Anulaciones de datos en vivo",
      "classroom.heading": "Vista de aula",
      "classroom.live_log": "Registro en vivo (auto-actualización)",
      "classroom.no_requests": "aún no hay solicitudes",
      "diff.heading": "Comparar dos sesiones",
      "diff.session_a": "Sesión A",
      "diff.session_b": "Sesión B",
      "diff.compare": "Comparar",
      "diff.added": "agregado",
      "diff.removed": "eliminado",
      "diff.shifted": "desplazado",
      "tooltip.lang": "Switch language",
      "common.backup": "Respaldar todo",
      "common.restore": "Restaurar respaldo",
      "common.export_csv": "Exportar CSV",
      "common.export_json": "Exportar JSON",
    },
  };

  const STORAGE_KEY = "uacj_lang";

  function getLang() {
    try {
      const v = localStorage.getItem(STORAGE_KEY);
      if (v === "en" || v === "es") return v;
    } catch (e) { /* ignore */ }
    // Auto-pick from navigator: Spanish if browser language starts with `es`,
    // otherwise English. Mexico defaults to es-MX.
    const nav = (navigator.language || "en").toLowerCase();
    return nav.startsWith("es") ? "es" : "en";
  }

  function setLang(l) {
    try { localStorage.setItem(STORAGE_KEY, l); } catch (e) { /* ignore */ }
  }

  function t(key) {
    const lang = getLang();
    const bank = STRINGS[lang] || STRINGS.en;
    return bank[key] !== undefined ? bank[key] : (STRINGS.en[key] !== undefined ? STRINGS.en[key] : key);
  }

  function applyI18n(root) {
    root = root || document;
    root.querySelectorAll("[data-i18n]").forEach((el) => {
      const key = el.getAttribute("data-i18n");
      el.textContent = t(key);
    });
    root.querySelectorAll("[data-i18n-placeholder]").forEach((el) => {
      const key = el.getAttribute("data-i18n-placeholder");
      el.setAttribute("placeholder", t(key));
    });
    root.querySelectorAll("[data-i18n-title]").forEach((el) => {
      const key = el.getAttribute("data-i18n-title");
      el.setAttribute("title", t(key));
    });
    if (document.documentElement) {
      document.documentElement.setAttribute("lang", getLang());
    }
  }

  function injectLanguageToggle() {
    if (document.getElementById("lang-toggle")) return;
    const header = document.querySelector("header");
    if (!header) return;
    const btn = document.createElement("button");
    btn.id = "lang-toggle";
    btn.className = "secondary";
    btn.setAttribute("data-i18n", "lang.toggle");
    btn.setAttribute("data-i18n-title", "tooltip.lang");
    btn.style.cssText = "margin-left:auto; padding:4px 10px; font-size:12px;";
    btn.textContent = t("lang.toggle");
    btn.title = t("tooltip.lang");
    btn.onclick = () => {
      setLang(getLang() === "en" ? "es" : "en");
      applyI18n();
      // Page-specific code listens for this event to redraw dynamic content.
      document.dispatchEvent(new CustomEvent("uacj:lang-changed"));
    };
    header.appendChild(btn);
  }

  // Expose to other scripts
  window.UACJ_I18N = { t, applyI18n, getLang, setLang };

  document.addEventListener("DOMContentLoaded", () => {
    injectLanguageToggle();
    applyI18n();
  });
})();
