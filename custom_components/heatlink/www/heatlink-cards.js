/*
 * HeatLink dashboard cards.
 *
 *  - "heatlink-tandem" tile card feature: one status line under a HeatLink
 *    thermostat tile (waiting for Tandem, parked, checking the room, ...).
 *  - "heatlink-house-card": every HeatLink head grouped by outdoor unit, with
 *    the unit's Tandem turn and a countdown.
 */

const CARD_VERSION = "0.1.0";
const FEATURE_TAG = "heatlink-tandem";
const HOUSE_TAG = "heatlink-house-card";

const MODE_COLORS = {
  heat: "var(--state-climate-heat-color, #ff8100)",
  cool: "var(--state-climate-cool-color, #2b9af9)",
  heat_cool: "var(--state-climate-heat_cool-color, #008000)",
  dry: "var(--state-climate-dry-color, #efbd07)",
  fan_only: "var(--state-climate-fan_only-color, #8a8a8a)",
  off: "var(--state-climate-off-color, #8a8a8a)",
};

/* ------------------------------------------------------------------ *
 * Helpers
 * ------------------------------------------------------------------ */

function isHeatLinkClimate(stateObj) {
  return (
    !!stateObj &&
    stateObj.entity_id.startsWith("climate.") &&
    stateObj.attributes.heatlink_uid !== undefined
  );
}

function minutesUntil(iso) {
  if (!iso) return null;
  const ms = Date.parse(iso) - Date.now();
  if (Number.isNaN(ms)) return null;
  return Math.max(0, Math.ceil(ms / 60000));
}

function inMinutes(n) {
  if (n === null) return "";
  if (n <= 1) return "within a minute";
  if (n < 60) return `in ${n} min`;
  const h = Math.floor(n / 60);
  const m = n % 60;
  return m ? `in ${h} h ${m} min` : `in ${h} h`;
}

/** The head's own status line, mirroring the HeatLink web page. */
function headStatus(stateObj) {
  if (!stateObj || stateObj.state === "unavailable") return null;
  const a = stateObj.attributes;
  if (a.held) {
    if (a.checking) return { text: "Checking if this room needs a turn", icon: "mdi:fan" };
    const n = minutesUntil(a.tandem_at);
    if (a.tandem === "switch") {
      const next = a.tandem_next === "cool" ? "cooling" : "heating";
      const eta = n === null ? "" : ` · ${next} starts ${inMinutes(n)}`;
      return { text: `Paused while modes switch${eta}`, icon: "mdi:swap-horizontal" };
    }
    const other = a.tandem === "cool" ? "cool" : "heat";
    const own = stateObj.state === "cool" ? "cooling" : "heating";
    const eta = n === null ? "" : ` · ${own} ${inMinutes(n)}`;
    return { text: `Paused while other rooms ${other}${eta}`, icon: "mdi:timer-sand" };
  }
  if (a.park_state === "probing") return { text: "Checking room", icon: "mdi:fan" };
  if (a.parked) {
    if (a.room_sensor) return { text: "Parked · room sensor", icon: "mdi:parking" };
    const n = minutesUntil(a.next_probe_at);
    const eta = n === null ? "" : ` · air check ${inMinutes(n)}`;
    return { text: `Parked${eta}`, icon: "mdi:parking" };
  }
  return null;
}

/** Find the outdoor unit's Tandem sensor for a head's climate entity. */
function tandemSensor(hass, climateId) {
  const ent = hass.entities && hass.entities[climateId];
  const device = ent && hass.devices && hass.devices[ent.device_id];
  const outdoor = device && device.via_device_id;
  if (!outdoor) return null;
  for (const e of Object.values(hass.entities)) {
    if (e.device_id === outdoor && e.platform === "heatlink" && e.translation_key === "tandem_state") {
      return hass.states[e.entity_id] || null;
    }
  }
  return null;
}

function tandemText(hass, sensor) {
  if (!sensor || ["unavailable", "unknown"].includes(sensor.state)) return null;
  const label = hass.formatEntityState ? hass.formatEntityState(sensor) : sensor.state;
  if (sensor.state === "off") return null;
  const n = minutesUntil(sensor.attributes.turn_ends_at);
  const waiting = (sensor.attributes.waiting || []).length;
  if (["heating", "cooling"].includes(sensor.state) && waiting && n !== null) {
    return `${label} · turn ends ${inMinutes(n)}`;
  }
  return label;
}

function fireMoreInfo(node, entityId) {
  node.dispatchEvent(
    new CustomEvent("hass-more-info", { detail: { entityId }, bubbles: true, composed: true })
  );
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}

/* ------------------------------------------------------------------ *
 * Tile card feature
 * ------------------------------------------------------------------ */

class HeatLinkTandemFeature extends HTMLElement {
  static getStubConfig() {
    return { type: `custom:${FEATURE_TAG}` };
  }

  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._timer = null;
  }

  setConfig(config) {
    if (!config) throw new Error("Invalid configuration");
    this._config = config;
  }

  set hass(hass) {
    this._hass = hass;
    this._render();
  }

  set stateObj(stateObj) {
    this._stateObj = stateObj;
    this._render();
  }

  set context(context) {
    this._context = context;
    this._render();
  }

  connectedCallback() {
    this._timer = setInterval(() => this._render(), 30000);
  }

  disconnectedCallback() {
    clearInterval(this._timer);
    this._timer = null;
  }

  _state() {
    if (this._stateObj) return this._stateObj;
    const id = this._context && this._context.entity_id;
    return id && this._hass ? this._hass.states[id] : null;
  }

  _render() {
    const stateObj = this._state();
    const status = isHeatLinkClimate(stateObj) ? headStatus(stateObj) : null;
    let text = status && status.text;
    let icon = status && status.icon;
    if (!text && stateObj && this._hass) {
      const sensor = tandemSensor(this._hass, stateObj.entity_id);
      const t = tandemText(this._hass, sensor);
      if (t) {
        text = `Tandem: ${t}`;
        icon = "mdi:sync";
      }
    }
    const key = `${text}|${icon}`;
    if (key === this._last) return;
    this._last = key;
    this.style.display = text ? "" : "none";
    this.shadowRoot.innerHTML = text
      ? `<style>
          .row { display:flex; align-items:center; gap:8px; padding:0 12px 12px;
                 font-size:14px; color: var(--secondary-text-color); }
          ha-icon { --mdc-icon-size:18px; flex:none; }
          span { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
        </style>
        <div class="row"><ha-icon icon="${icon}"></ha-icon><span>${escapeHtml(text)}</span></div>`
      : "";
  }
}

/* ------------------------------------------------------------------ *
 * House card
 * ------------------------------------------------------------------ */

class HeatLinkHouseCard extends HTMLElement {
  static getStubConfig() {
    return {};
  }

  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._timer = null;
  }

  setConfig(config) {
    this._config = { ...(config || {}) };
    this._last = null;
  }

  set hass(hass) {
    this._hass = hass;
    this._render();
  }

  connectedCallback() {
    this._timer = setInterval(() => {
      this._last = null;
      this._render();
    }, 30000);
  }

  disconnectedCallback() {
    clearInterval(this._timer);
    this._timer = null;
  }

  getCardSize() {
    return 1 + this._heads().length;
  }

  getGridOptions() {
    return { columns: 12, min_columns: 6 };
  }

  _heads() {
    const hass = this._hass;
    if (!hass) return [];
    const wanted = this._config.entities;
    return Object.values(hass.states)
      .filter((s) => isHeatLinkClimate(s) && (!wanted || wanted.includes(s.entity_id)))
      .sort((a, b) => (a.attributes.friendly_name || "").localeCompare(b.attributes.friendly_name || ""));
  }

  _render() {
    const hass = this._hass;
    if (!hass) return;
    const heads = this._heads();
    const groups = new Map();
    for (const s of heads) {
      const key = s.attributes.outdoor_unit || `solo:${s.entity_id}`;
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(s);
    }
    const unit = (s) => s.attributes.temperature_unit || hass.config.unit_system.temperature;
    const fmt = (v) => (v === null || v === undefined ? "–" : Number(v).toFixed(1));

    let html = "";
    for (const [key, members] of groups) {
      if (!key.startsWith("solo:")) {
        const sensor = tandemSensor(hass, members[0].entity_id);
        const ent = sensor && hass.entities[sensor.entity_id];
        const device = ent && hass.devices[ent.device_id];
        const name = (device && (device.name_by_user || device.name)) || "Outdoor unit";
        const t = tandemText(hass, sensor);
        html += `<div class="unit" data-entity="${sensor ? sensor.entity_id : ""}">
            <ha-icon icon="mdi:hvac"></ha-icon><span class="name">${escapeHtml(name)}</span>
            ${t ? `<span class="tandem">${escapeHtml(t)}</span>` : ""}</div>`;
      }
      for (const s of members) {
        const status = headStatus(s);
        const action = s.attributes.hvac_action;
        const mode = s.state;
        const active = ["heating", "cooling", "drying", "fan", "preheating", "defrosting"].includes(action);
        const color = mode === "off" || !active ? MODE_COLORS.off : MODE_COLORS[mode] || MODE_COLORS.off;
        const label = hass.formatEntityAttributeValue && action
          ? hass.formatEntityAttributeValue(s, "hvac_action")
          : hass.formatEntityState ? hass.formatEntityState(s) : mode;
        const name = s.attributes.friendly_name || s.entity_id;
        html += `<div class="head" data-entity="${s.entity_id}">
            <ha-state-icon class="icon" style="color:${color}"></ha-state-icon>
            <div class="info"><div class="name">${escapeHtml(name)}</div>
              <div class="sub">${escapeHtml(status ? status.text : mode === "off" ? (hass.formatEntityState ? hass.formatEntityState(s) : "Off") : label)}</div></div>
            <div class="temps"><span class="cur">${fmt(s.attributes.current_temperature)} ${unit(s)}</span>
              ${mode === "off" ? "" : s.attributes.target_temp_low != null && s.attributes.target_temp_high != null
                ? `<span class="tgt">→ ${fmt(s.attributes.target_temp_low)}–${fmt(s.attributes.target_temp_high)}</span>`
                : s.attributes.temperature != null ? `<span class="tgt">→ ${fmt(s.attributes.temperature)}</span>` : ""}</div>
          </div>`;
      }
    }
    if (!heads.length) html = `<div class="empty">No HeatLink heads found.</div>`;
    if (html === this._last) return;
    this._last = html;
    const title = this._config.title;
    this.shadowRoot.innerHTML = `
      <style>
        ha-card { padding: 8px 0; }
        h1 { font-size: 20px; font-weight: 400; margin: 8px 16px 4px; }
        .unit { display:flex; align-items:center; gap:8px; margin: 10px 16px 2px;
                color: var(--secondary-text-color); font-size: 13px; text-transform: uppercase;
                letter-spacing: .04em; cursor: pointer; }
        .unit ha-icon { --mdc-icon-size: 18px; }
        .unit .tandem { margin-left: auto; text-transform: none; letter-spacing: 0; }
        .head { display:flex; align-items:center; gap:12px; padding: 8px 16px; cursor: pointer; }
        .head:hover { background: var(--secondary-background-color); }
        .icon { --mdc-icon-size: 24px; flex: none; }
        .info { flex: 1; min-width: 0; }
        .name { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
        .sub { color: var(--secondary-text-color); font-size: 13px;
               overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
        .temps { text-align: right; white-space: nowrap; }
        .tgt { display:block; color: var(--secondary-text-color); font-size: 13px; }
        .empty { padding: 16px; color: var(--secondary-text-color); }
      </style>
      <ha-card>${title ? `<h1>${escapeHtml(title)}</h1>` : ""}${html}</ha-card>`;
    this.shadowRoot.querySelectorAll("ha-state-icon").forEach((el) => {
      const id = el.closest(".head").dataset.entity;
      el.hass = hass;
      el.stateObj = hass.states[id];
    });
    this.shadowRoot.querySelectorAll("[data-entity]").forEach((el) => {
      if (el.dataset.entity) el.addEventListener("click", () => fireMoreInfo(this, el.dataset.entity));
    });
  }
}

/* ------------------------------------------------------------------ *
 * Registration
 * ------------------------------------------------------------------ */

let definedRegistry = null;

function defineHeatLinkCards() {
  const registry = window.customElements;
  if (!registry || definedRegistry === registry) return;
  definedRegistry = registry;
  try {
    if (!registry.get(FEATURE_TAG)) registry.define(FEATURE_TAG, HeatLinkTandemFeature);
    if (!registry.get(HOUSE_TAG)) registry.define(HOUSE_TAG, HeatLinkHouseCard);
  } catch (err) {
    // Another copy already claimed the tags on this registry.
  }
}

defineHeatLinkCards();
const heatlinkDefineTimer = setInterval(defineHeatLinkCards, 200);
setTimeout(() => clearInterval(heatlinkDefineTimer), 60000);

window.customCardFeatures = window.customCardFeatures || [];
if (!window.customCardFeatures.some((f) => f.type === FEATURE_TAG)) {
  window.customCardFeatures.push({
    type: FEATURE_TAG,
    name: "HeatLink Tandem status",
    configurable: false,
    // Older frontends pass the state object; newer ones pass (hass, context).
    supported: (a, b) => {
      if (b && b.entity_id) return isHeatLinkClimate(a && a.states ? a.states[b.entity_id] : null);
      return isHeatLinkClimate(a);
    },
  });
}

window.customCards = window.customCards || [];
if (!window.customCards.some((c) => c.type === HOUSE_TAG)) {
  window.customCards.push({
    type: HOUSE_TAG,
    name: "HeatLink house",
    description: "Every HeatLink head grouped by outdoor unit, with Tandem turns.",
    preview: true,
  });
}

console.info(
  `%c HEATLINK-CARDS %c ${CARD_VERSION} `,
  "background:#e65100;color:#fff;border-radius:3px 0 0 3px",
  "background:#455a64;color:#fff;border-radius:0 3px 3px 0"
);
