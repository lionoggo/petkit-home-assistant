const PETKIT_CARD_VERSION = "0.5.0";
const HLS_SDK_URL = `/petkit_static/hls.light.min.js?v=${PETKIT_CARD_VERSION}`;
const RTM_SDK_URL = `/petkit_static/agora-rtm-2.3.0.js?v=${PETKIT_CARD_VERSION}`;
const HIDDEN_STOP_DELAY_MS = 30_000;
const CAMERA_SESSION_MAX_MS = 15 * 60_000;
const RTM_IDLE_LOGOUT_MS = 60_000;
const AUTH_REFRESH_MARGIN_MS = 5 * 60_000;
const AUTH_REFRESH_INTERVAL_MS = 60_000;

const scriptPromises = new Map();

function loadScript(url, globalName) {
  if (window[globalName]) {
    return Promise.resolve(window[globalName]);
  }
  if (scriptPromises.has(url)) {
    return scriptPromises.get(url);
  }
  const promise = new Promise((resolve, reject) => {
    const script = document.createElement("script");
    script.src = url;
    script.async = true;
    script.onload = () => {
      if (window[globalName]) {
        resolve(window[globalName]);
      } else {
        reject(new Error(`SDK ${globalName} did not load`));
      }
    };
    script.onerror = () => reject(new Error(`Unable to load ${url}`));
    document.head.appendChild(script);
  }).catch((error) => {
    scriptPromises.delete(url);
    throw error;
  });
  scriptPromises.set(url, promise);
  return promise;
}

function normalizeServiceResponse(value) {
  return value?.response ?? value?.data ?? value;
}

function requestCameraStream(hass, serviceData) {
  return hass.callService(
    "petkit",
    "start_camera_stream",
    serviceData,
    undefined,
    false,
    true,
  );
}

function stopCameraStream(hass, sessionId) {
  return hass.callService("petkit", "stop_camera_stream", {
    session_id: sessionId,
  });
}

async function stopUnusedCameraSession(hass, response) {
  if (!response?.session_id || !hass) {
    return;
  }
  try {
    await stopCameraStream(hass, String(response.session_id));
  } catch {
    // The sidecar session may already have expired.
  }
}

function mediaAuthorizationHeaders(hass) {
  const accessToken = hass?.auth?.data?.access_token;
  return accessToken ? { Authorization: `Bearer ${accessToken}` } : {};
}

async function refreshMediaAuthorization(hass) {
  const auth = hass?.auth;
  const expiresAt = Number(auth?.data?.expires);
  const expiresSoon =
    Number.isFinite(expiresAt) &&
    expiresAt - Date.now() < AUTH_REFRESH_MARGIN_MS;
  if (
    (auth?.expired || expiresSoon) &&
    typeof auth.refreshAccessToken === "function" &&
    auth.data?.refresh_token
  ) {
    await auth.refreshAccessToken();
  }
}

async function waitForMediaPlaylist(
  url,
  hass,
  { timeoutMs = 20_000, intervalMs = 250 } = {},
) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      const response = await fetch(url, {
        headers: mediaAuthorizationHeaders(hass),
        cache: "no-store",
      });
      if (response.ok && (await response.text()).trim()) {
        return;
      }
    } catch {
      // The sidecar may not have created the manifest yet.
    }
    await new Promise((resolve) => setTimeout(resolve, intervalMs));
  }
  throw new Error("Camera media is not ready");
}

function startLiveCommand(isSd) {
  return JSON.stringify({
    cmd: "start_live",
    payload: { isSD: isSd ? 1 : 0 },
  });
}

function stopLiveCommand() {
  return JSON.stringify({ cmd: "stop_live" });
}

function liveHeartbeatCommand(isSd) {
  return JSON.stringify({
    cmd: "live_heartbeat",
    payload: { isSD: isSd ? 1 : 0 },
  });
}

function canReuseRtm(userId, access) {
  return Boolean(userId && userId === String(access?.app_rtm_user_id || ""));
}

function settleWithTimeout(promise, timeoutMs) {
  return Promise.race([
    Promise.resolve(promise).catch(() => undefined),
    new Promise((resolve) => setTimeout(resolve, timeoutMs)),
  ]);
}

function secondsToInputValue(value) {
  const numeric = Number(value);
  if (!Number.isInteger(numeric) || numeric < 0 || numeric > 86399) {
    return "00:00";
  }
  const hours = String(Math.floor(numeric / 3600)).padStart(2, "0");
  const minutes = String(Math.floor((numeric % 3600) / 60)).padStart(2, "0");
  return `${hours}:${minutes}`;
}

function inputValueToSeconds(value) {
  const match = /^(\d{2}):(\d{2})$/.exec(String(value || ""));
  if (!match) {
    return NaN;
  }
  return Number(match[1]) * 3600 + Number(match[2]) * 60;
}

function cloneSchedule(value) {
  return JSON.parse(JSON.stringify(value ?? []));
}

function validateScheduleDraft(days) {
  if (!Array.isArray(days) || days.length !== 7) {
    throw new Error("Schedule must contain seven days");
  }
  const repeats = new Set();
  for (const day of days) {
    const weekday = Number(day.repeats);
    if (weekday < 1 || weekday > 7 || repeats.has(weekday)) {
      throw new Error("Each weekday must occur exactly once");
    }
    repeats.add(weekday);
    const times = new Set();
    for (const item of day.items ?? []) {
      const seconds = Number(item.time);
      const amount1 = Number(item.amount1);
      const amount2 = Number(item.amount2);
      if (
        !Number.isInteger(seconds) ||
        seconds < 0 ||
        seconds > 86399 ||
        seconds % 60
      ) {
        throw new Error("Feed time must use whole minutes");
      }
      if (times.has(seconds)) {
        throw new Error("Feed times must be unique within a day");
      }
      times.add(seconds);
      if (
        !Number.isInteger(amount1) ||
        !Number.isInteger(amount2) ||
        amount1 < 0 ||
        amount2 < 0 ||
        amount1 > 10 ||
        amount2 > 10 ||
        amount1 + amount2 === 0
      ) {
        throw new Error("A meal needs 1-10 portions across its two bins");
      }
    }
  }
  return days;
}

function scheduleFromState(state) {
  const source = cloneSchedule(state?.attributes?.schedule);
  if (source.length === 7) {
    return source;
  }
  return [1, 2, 3, 4, 5, 6, 7].map((repeats) => ({
    repeats,
    suspended: 0,
    items: [],
  }));
}

function localTranslator(hass) {
  const chinese = String(hass?.locale?.language || "en").startsWith("zh");
  const text = chinese
    ? {
        scheduleTitle: "小佩喂食计划",
        cameraTitle: "小佩实时画面",
        save: "保存",
        discard: "放弃修改",
        reload: "重新载入",
        addMeal: "添加一餐",
        copyAll: "复制到今天之外的全部日期",
        paused: "暂停当天计划",
        bin1: "1 仓",
        bin2: "2 仓",
        time: "时间",
        delete: "删除",
        dirty: "有未保存修改",
        saved: "已保存",
        conflict: "计划已在其他客户端改变，请重新载入",
        unavailable: "计划实体不可用",
        noMeals: "当天没有餐次",
        play: "播放",
        stop: "停止",
        mute: "静音",
        sound: "开启声音",
        retry: "重试",
        idle: "点击播放后建立视频连接",
        connecting: "正在连接摄像头…",
        playing: "实时视频播放中",
        stopped: "视频已停止",
        videoError: "视频连接失败",
        cameraHint: "音视频通过 PETKIT 云和本机原生媒体服务传输。",
        weekday: ["周一", "周二", "周三", "周四", "周五", "周六", "周日"],
      }
    : {
        scheduleTitle: "PETKIT feeding schedule",
        cameraTitle: "PETKIT live camera",
        save: "Save",
        discard: "Discard",
        reload: "Reload",
        addMeal: "Add meal",
        copyAll: "Copy to all other days",
        paused: "Pause this day",
        bin1: "Bin 1",
        bin2: "Bin 2",
        time: "Time",
        delete: "Delete",
        dirty: "Unsaved changes",
        saved: "Saved",
        conflict: "The schedule changed elsewhere. Reload it first.",
        unavailable: "Plan entity is unavailable",
        noMeals: "No meals for this day",
        play: "Play",
        stop: "Stop",
        mute: "Mute",
        sound: "Sound on",
        retry: "Retry",
        idle: "Start playback to open a camera session",
        connecting: "Connecting to camera…",
        playing: "Live video and audio playing",
        stopped: "Video stopped",
        videoError: "Camera connection failed",
        cameraHint: "Live media uses PETKIT cloud and the local native media service.",
        weekday: ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
      };
  return (key) => text[key] ?? key;
}

class PetkitScheduleCard extends HTMLElement {
  static getStubConfig(hass) {
    const state = Object.values(hass?.states ?? {}).find(
      (candidate) =>
        candidate.attributes?.device_id && candidate.attributes?.schedule,
    );
    return { entity: state?.entity_id || "" };
  }

  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._config = {};
    this._hass = null;
    this._days = [];
    this._scheduleHash = "";
    this._deviceId = "";
    this._selectedDay = 0;
    this._dirty = false;
    this._busy = false;
    this._errorMessage = "";
    this._successMessage = "";
    this._renderShell();
  }

  setConfig(config) {
    if (!config?.entity) {
      throw new Error("petkit-schedule-card requires a plan entity");
    }
    this._config = config;
    this._syncFromHass();
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
    this._syncFromHass();
    this._render();
  }

  getCardSize() {
    return 10;
  }

  _renderShell() {
    const style = document.createElement("style");
    style.textContent = `
      :host { display: block; }
      ha-card { overflow: hidden; }
      .header, .toolbar, .day-tabs, .day-meta { display: flex; align-items: center; gap: 10px; }
      .header { justify-content: space-between; padding: 16px; }
      h2 { margin: 0; font-size: 18px; }
      .status { color: var(--secondary-text-color); font-size: 13px; }
      .toolbar { padding: 0 16px 12px; flex-wrap: wrap; }
      .day-tabs { padding: 0 16px 12px; overflow-x: auto; }
      button, select, input {
        min-height: 36px; border-radius: 8px; border: 1px solid var(--divider-color);
        background: var(--card-background-color); color: var(--primary-text-color);
        font: inherit; padding: 6px 10px; box-sizing: border-box;
      }
      button { cursor: pointer; }
      button.primary { background: var(--primary-color); color: var(--text-primary-color); border-color: var(--primary-color); }
      button.day { white-space: nowrap; }
      button.day[selected] { background: var(--primary-color); color: var(--text-primary-color); }
      button:disabled { opacity: .5; cursor: default; }
      .content { padding: 0 16px 16px; }
      .day-meta { justify-content: space-between; padding: 10px 0; }
      .meals { display: grid; gap: 10px; }
      .meal {
        display: grid; grid-template-columns: minmax(110px, 1fr) repeat(2, minmax(90px, .6fr)) auto;
        gap: 8px; align-items: end; padding: 12px;
        border: 1px solid var(--divider-color); border-radius: 12px;
      }
      label { display: grid; gap: 5px; color: var(--secondary-text-color); font-size: 12px; }
      .empty { color: var(--secondary-text-color); padding: 18px 0; }
      .error { color: var(--error-color); margin: 8px 0; }
      .success { color: var(--success-color); margin: 8px 0; }
      @media (max-width: 560px) {
        .meal { grid-template-columns: 1fr 1fr; }
        .meal .time-field { grid-column: 1 / -1; }
      }
    `;
    const card = document.createElement("ha-card");
    card.innerHTML = `
      <div class="header"><div><h2></h2><div class="status"></div></div></div>
      <div class="toolbar">
        <button class="save primary"></button>
        <button class="discard"></button>
        <button class="reload"></button>
        <button class="add"></button>
        <button class="copy"></button>
      </div>
      <div class="day-tabs"></div>
      <div class="content"><div class="message"></div><div class="day-meta"></div><div class="meals"></div></div>
    `;
    this.shadowRoot.append(style, card);
    this._card = card;
    this._title = card.querySelector("h2");
    this._status = card.querySelector(".status");
    this._message = card.querySelector(".message");
    this._dayTabs = card.querySelector(".day-tabs");
    this._dayMeta = card.querySelector(".day-meta");
    this._meals = card.querySelector(".meals");
    card.querySelector(".save").addEventListener("click", () => this._save());
    card.querySelector(".discard").addEventListener("click", () => this._discard());
    card.querySelector(".reload").addEventListener("click", () => this._reload());
    card.querySelector(".add").addEventListener("click", () => this._addMeal());
    card.querySelector(".copy").addEventListener("click", () => this._copyDay());
  }

  _syncFromHass() {
    if (!this._hass || !this._config.entity || this._dirty) {
      return;
    }
    const state = this._hass.states[this._config.entity];
    this._days = scheduleFromState(state);
    this._scheduleHash = String(state?.attributes?.hash || "");
    this._deviceId = String(state?.attributes?.device_id || "");
    if (this._selectedDay >= this._days.length) {
      this._selectedDay = 0;
    }
  }

  _render() {
    if (!this._hass) {
      return;
    }
    const t = localTranslator(this._hass);
    const state = this._hass.states[this._config.entity];
    this._title.textContent = this._config.title || t("scheduleTitle");
    this._status.textContent = this._busy
      ? t("connecting")
      : this._dirty
        ? t("dirty")
        : state
          ? t("saved")
          : t("unavailable");
    this._message.className = "message";
    this._message.textContent = "";
    if (this._errorMessage) {
      this._message.classList.add("error");
      this._message.textContent = this._errorMessage;
    } else if (this._successMessage) {
      this._message.classList.add("success");
      this._message.textContent = this._successMessage;
    }
    if (!state || !this._deviceId) {
      this._message.classList.add("error");
      this._message.textContent = t("unavailable");
    }
    const buttons = this._card.querySelectorAll(".toolbar button");
    const labels = [t("save"), t("discard"), t("reload"), t("addMeal"), t("copyAll")];
    buttons.forEach((button, index) => {
      button.textContent = labels[index];
      button.disabled = this._busy;
    });
    buttons[0].disabled = this._busy || !this._dirty || !this._deviceId;
    buttons[1].disabled = !this._dirty;
    buttons[3].disabled = this._busy || !this._deviceId;
    buttons[4].disabled = this._busy || !this._deviceId;
    this._renderDayTabs(t);
    this._renderDay(t);
  }

  _renderDayTabs(t) {
    this._dayTabs.replaceChildren();
    this._days.forEach((day, index) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "day";
      button.textContent = t("weekday")[Number(day.repeats) - 1];
      button.toggleAttribute("selected", index === this._selectedDay);
      button.addEventListener("click", () => {
        this._selectedDay = index;
        this._render();
      });
      this._dayTabs.append(button);
    });
  }

  _renderDay(t) {
    this._dayMeta.replaceChildren();
    this._meals.replaceChildren();
    const day = this._days[this._selectedDay];
    if (!day) {
      return;
    }
    const pausedLabel = document.createElement("label");
    const paused = document.createElement("input");
    paused.type = "checkbox";
    paused.checked = Number(day.suspended) === 1;
    paused.addEventListener("change", () => {
      day.suspended = paused.checked ? 1 : 0;
      this._markDirty();
    });
    pausedLabel.append(paused, document.createTextNode(` ${t("paused")}`));
    this._dayMeta.append(pausedLabel);

    const items = day.items ?? [];
    if (!items.length) {
      const empty = document.createElement("div");
      empty.className = "empty";
      empty.textContent = t("noMeals");
      this._meals.append(empty);
      return;
    }
    items.forEach((item, itemIndex) => {
      const meal = document.createElement("div");
      meal.className = "meal";

      const timeLabel = document.createElement("label");
      timeLabel.className = "time-field";
      timeLabel.textContent = t("time");
      const time = document.createElement("input");
      time.type = "time";
      time.step = "60";
      time.value = secondsToInputValue(item.time);
      time.addEventListener("change", () => {
        const seconds = inputValueToSeconds(time.value);
        if (Number.isFinite(seconds)) {
          item.time = seconds;
          item.id = String(seconds);
          this._markDirty();
        }
      });
      timeLabel.append(time);

      const bin1Label = document.createElement("label");
      bin1Label.textContent = t("bin1");
      const bin1 = document.createElement("input");
      bin1.type = "number";
      bin1.min = "0";
      bin1.max = "10";
      bin1.step = "1";
      bin1.value = String(item.amount1 ?? 0);
      bin1.addEventListener("input", () => {
        item.amount1 = Number(bin1.value);
        this._markDirty();
      });
      bin1Label.append(bin1);

      const bin2Label = document.createElement("label");
      bin2Label.textContent = t("bin2");
      const bin2 = document.createElement("input");
      bin2.type = "number";
      bin2.min = "0";
      bin2.max = "10";
      bin2.step = "1";
      bin2.value = String(item.amount2 ?? 0);
      bin2.addEventListener("input", () => {
        item.amount2 = Number(bin2.value);
        this._markDirty();
      });
      bin2Label.append(bin2);

      const remove = document.createElement("button");
      remove.type = "button";
      remove.textContent = t("delete");
      remove.addEventListener("click", () => {
        items.splice(itemIndex, 1);
        this._markDirty();
      });

      meal.append(timeLabel, bin1Label, bin2Label, remove);
      this._meals.append(meal);
    });
  }

  _markDirty() {
    this._dirty = true;
    this._successMessage = "";
    this._render();
  }

  _addMeal() {
    const day = this._days[this._selectedDay];
    if (!day) {
      return;
    }
    const used = new Set((day.items ?? []).map((item) => Number(item.time)));
    let seconds = 8 * 3600;
    while (used.has(seconds) && seconds < 23 * 3600) {
      seconds += 3600;
    }
    day.items = day.items ?? [];
    day.items.push({
      id: String(seconds),
      time: seconds,
      amount1: 1,
      amount2: 0,
      name: "",
    });
    this._markDirty();
  }

  _copyDay() {
    const source = this._days[this._selectedDay];
    if (!source) {
      return;
    }
    this._days.forEach((day, index) => {
      if (index === this._selectedDay) {
        return;
      }
      day.suspended = source.suspended;
      day.items = cloneSchedule(source.items ?? []);
    });
    this._markDirty();
  }

  _discard() {
    this._dirty = false;
    this._errorMessage = "";
    this._successMessage = "";
    this._syncFromHass();
    this._render();
  }

  async _reload() {
    this._dirty = false;
    this._busy = true;
    this._successMessage = "";
    this._errorMessage = "";
    this._render();
    try {
      await this._hass.callService("petkit", "refresh_schedule", {
        device_id: this._deviceId,
      });
      this._syncFromHass();
    } catch (error) {
      this._errorMessage = String(error?.message || error);
    } finally {
      this._busy = false;
      this._render();
    }
  }

  async _save() {
    const t = localTranslator(this._hass);
    this._busy = true;
    this._render();
    try {
      validateScheduleDraft(this._days);
      await this._hass.callService("petkit", "save_schedule", {
        device_id: this._deviceId,
        days: this._days,
        expected_hash: this._scheduleHash,
      });
      this._dirty = false;
      this._errorMessage = "";
      this._successMessage = t("saved");
    } catch (error) {
      this._successMessage = "";
      this._errorMessage = String(error?.message || error).includes(
        "schedule_changed",
      )
        ? t("conflict")
        : String(error?.message || error);
    } finally {
      this._busy = false;
      this._render();
    }
  }
}

class PetkitCameraCard extends HTMLElement {
  static getStubConfig() {
    return { title: "PETKIT camera", quality: "hd" };
  }

  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._config = {};
    this._hass = null;
    this._runId = 0;
    this._busy = false;
    this._sessionId = "";
    this._access = null;
    this._rtm = null;
    this._rtmUserId = "";
    this._rtmIdleTimer = null;
    this._authTimer = null;
    this._heartbeatTimer = null;
    this._hls = null;
    this._audioHls = null;
    this._video = null;
    this._audio = null;
    this._videoReady = false;
    this._audioReady = false;
    this._playStarted = false;
    this._sessionTimer = null;
    this._hiddenTimer = null;
    this._muted = false;
    this._status = "idle";
    this._error = "";
    this._visibilityHandler = () => {
      if (document.hidden) {
        this._hiddenTimer = setTimeout(() => this._stop(), HIDDEN_STOP_DELAY_MS);
      } else if (this._hiddenTimer) {
        clearTimeout(this._hiddenTimer);
        this._hiddenTimer = null;
      }
    };
    this._renderShell();
    this._render();
  }

  setConfig(config) {
    if (config !== undefined && (!config || typeof config !== "object")) {
      throw new Error("petkit-camera-card config must be an object");
    }
    this._config = config || {};
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
    this._render();
  }

  connectedCallback() {
    document.addEventListener("visibilitychange", this._visibilityHandler);
  }

  disconnectedCallback() {
    document.removeEventListener("visibilitychange", this._visibilityHandler);
    void this._stop({ logoutRtm: true });
  }

  getCardSize() {
    return 7;
  }

  _renderShell() {
    const style = document.createElement("style");
    style.textContent = `
      :host { display: block; }
      ha-card { overflow: hidden; }
      .header, .footer { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 14px 16px; }
      h2 { margin: 0; font-size: 18px; }
      .status { display: flex; align-items: center; gap: 7px; color: var(--secondary-text-color); font-size: 13px; margin-top: 4px; }
      .dot { width: 8px; height: 8px; border-radius: 50%; background: var(--disabled-text-color); }
      .dot.connecting { background: var(--warning-color); animation: pulse 1s infinite alternate; }
      .dot.playing { background: var(--success-color); }
      .dot.error { background: var(--error-color); }
      .actions { display: flex; gap: 8px; }
      button {
        min-height: 36px; padding: 0 14px; border-radius: 18px;
        border: 1px solid var(--divider-color); background: transparent;
        color: var(--primary-color); font: inherit; cursor: pointer;
      }
      button.primary { background: var(--primary-color); color: var(--text-primary-color); border-color: var(--primary-color); }
      button:disabled { opacity: .5; cursor: default; }
      .video-shell { position: relative; aspect-ratio: 16 / 9; min-height: 200px; background: #080a0d; }
      #player { position: absolute; inset: 0; }
      #player video { width: 100%; height: 100%; object-fit: contain; }
      .placeholder { position: absolute; inset: 0; display: grid; place-content: center; gap: 8px; padding: 20px; color: rgba(255,255,255,.78); text-align: center; pointer-events: none; }
      .placeholder[hidden] { display: none; }
      .error { color: #ffb4ab; max-width: 520px; }
      .footer { color: var(--secondary-text-color); font-size: 12px; }
      @keyframes pulse { from { opacity: .35; } to { opacity: 1; } }
    `;
    const card = document.createElement("ha-card");
    card.innerHTML = `
      <div class="header">
        <div><h2></h2><div class="status"><span class="dot"></span><span class="status-text"></span></div></div>
        <div class="actions">
          <button class="play primary" type="button"></button>
          <button class="stop" type="button" hidden></button>
          <button class="mute" type="button" hidden></button>
        </div>
      </div>
      <div class="video-shell">
        <div id="player"></div>
        <div class="placeholder"><strong class="placeholder-title"></strong><span class="placeholder-detail"></span></div>
      </div>
      <div class="footer"><span class="hint"></span><span class="quality"></span></div>
    `;
    this.shadowRoot.append(style, card);
    this._card = card;
    this._title = card.querySelector("h2");
    this._dot = card.querySelector(".dot");
    this._statusText = card.querySelector(".status-text");
    this._placeholder = card.querySelector(".placeholder");
    this._placeholderTitle = card.querySelector(".placeholder-title");
    this._placeholderDetail = card.querySelector(".placeholder-detail");
    this._player = card.querySelector("#player");
    this._playButton = card.querySelector(".play");
    this._stopButton = card.querySelector(".stop");
    this._muteButton = card.querySelector(".mute");
    this._playButton.addEventListener("click", () => this._play());
    this._stopButton.addEventListener("click", () => this._stop());
    this._muteButton.addEventListener("click", () => this._toggleMute());
  }

  _render() {
    const t = localTranslator(this._hass);
    this._title.textContent = this._config.title || t("cameraTitle");
    this._playButton.textContent =
      this._status === "error" ? t("retry") : t("play");
    this._stopButton.textContent = t("stop");
    this._muteButton.textContent = this._muted ? t("sound") : t("mute");
    this._statusText.textContent =
      {
        idle: t("idle"),
        connecting: t("connecting"),
        playing: t("playing"),
        stopped: t("stopped"),
        error: t("videoError"),
      }[this._status] || this._status;
    this._dot.className = `dot ${this._status}`;
    this._playButton.hidden = this._status === "playing" || this._busy;
    this._stopButton.hidden = this._status !== "playing" && this._status !== "connecting";
    this._muteButton.hidden = this._status !== "playing";
    this._playButton.disabled = this._busy || !this._hass;
    this._placeholder.hidden = this._status === "playing";
    this._placeholderTitle.textContent =
      this._status === "error" ? t("videoError") : t("cameraTitle");
    this._placeholderDetail.className = this._status === "error" ? "error" : "";
    this._placeholderDetail.textContent = this._error || t("idle");
    this._card.querySelector(".hint").textContent = t("cameraHint");
    this._card.querySelector(".quality").textContent =
      String(this._config.quality || "hd").toUpperCase();
  }

  async _play() {
    if (!this._hass || this._busy) {
      return;
    }
    const runId = ++this._runId;
    this._busy = true;
    this._status = "connecting";
    this._error = "";
    this._render();
    try {
      const Hls = await loadScript(HLS_SDK_URL, "Hls");
      await loadScript(RTM_SDK_URL, "AgoraRTM");
      const serviceData = {
        quality: String(this._config.quality || "hd").toLowerCase() === "sd" ? 1 : 2,
      };
      if (this._config.device_id) {
        serviceData.device_id = String(this._config.device_id);
      }
      const response = normalizeServiceResponse(
        await requestCameraStream(this._hass, serviceData),
      );
      if (runId !== this._runId) {
        await stopUnusedCameraSession(this._hass, response);
        return;
      }
      this._sessionId = response?.session_id
        ? String(response.session_id)
        : "";
      this._access = response;
      if (
        !response?.session_id ||
        !response?.video_playlist_url ||
        !response?.audio_playlist_url ||
        !response?.rtm_token ||
        !response?.app_rtm_user_id ||
        !response?.device_rtm_user_id
      ) {
        throw new Error("Camera stream response was incomplete");
      }
      if (!(await this._startRtm(response, runId))) {
        await stopUnusedCameraSession(this._hass, response);
        if (runId === this._runId) {
          this._sessionId = "";
          this._access = null;
        }
        return;
      }
      const expiresIn = Math.max(60, Number(response.expires_in) || 900);
      this._sessionTimer = setTimeout(
        () => this._stop(),
        Math.min(expiresIn * 1000, CAMERA_SESSION_MAX_MS),
      );
      await refreshMediaAuthorization(this._hass);
      if (runId !== this._runId) {
        return;
      }
      this._startAuthRefresh();
      const videoPlaylistUrl = String(response.video_playlist_url);
      const audioPlaylistUrl = String(response.audio_playlist_url);
      await Promise.all([
        waitForMediaPlaylist(videoPlaylistUrl, this._hass),
        waitForMediaPlaylist(audioPlaylistUrl, this._hass),
      ]);
      if (runId !== this._runId) {
        return;
      }
      this._startHls(
        Hls,
        videoPlaylistUrl,
        audioPlaylistUrl,
        runId,
      );
    } catch (error) {
      if (runId !== this._runId) {
        return;
      }
      this._error = String(error?.message || error);
      this._status = "error";
      await this._cleanupMedia(runId, { sendStop: true });
    } finally {
      if (runId === this._runId) {
        this._busy = false;
        this._render();
      }
    }
  }

  async _startRtm(access, runId) {
    const rtmGlobal = window.AgoraRTM;
    const RTM = rtmGlobal?.RTM || rtmGlobal?.default?.RTM;
    if (!RTM) {
      throw new Error("Agora RTM SDK is unavailable");
    }
    this._clearRtmIdleTimer();
    let rtm = this._rtm;
    if (!canReuseRtm(this._rtmUserId, access)) {
      await this._logoutRtm();
      const candidate = new RTM(
        String(access.app_id),
        String(access.app_rtm_user_id),
      );
      await candidate.login({ token: access.rtm_token });
      if (runId !== this._runId) {
        await settleWithTimeout(candidate.logout(), 2_000);
        return false;
      }
      this._rtm = candidate;
      this._rtmUserId = String(access.app_rtm_user_id);
      rtm = candidate;
    }
    if (!rtm) {
      return false;
    }
    if (runId !== this._runId) {
      return false;
    }
    await rtm.publish(
      String(access.device_rtm_user_id),
      startLiveCommand(Number(access.quality) === 1),
      { channelType: "USER" },
    );
    if (runId === this._runId) {
      this._startHeartbeat(Number(access.quality) === 1);
      return true;
    }
    return false;
  }

  _startHeartbeat(isSd) {
    this._stopHeartbeat();
    const send = () => {
      if (!this._rtm || !this._access) {
        return;
      }
      this._rtm
        .publish(
          String(this._access.device_rtm_user_id),
          liveHeartbeatCommand(isSd),
          { channelType: "USER" },
        )
        .catch(() => {});
    };
    send();
    this._heartbeatTimer = setInterval(send, 500);
  }

  _stopHeartbeat() {
    if (this._heartbeatTimer) {
      clearInterval(this._heartbeatTimer);
      this._heartbeatTimer = null;
    }
  }

  _clearRtmIdleTimer() {
    if (this._rtmIdleTimer) {
      clearTimeout(this._rtmIdleTimer);
      this._rtmIdleTimer = null;
    }
  }

  _startAuthRefresh() {
    if (this._authTimer) {
      clearInterval(this._authTimer);
    }
    this._authTimer = setInterval(() => {
      void refreshMediaAuthorization(this._hass);
    }, AUTH_REFRESH_INTERVAL_MS);
  }

  _scheduleRtmLogout() {
    this._clearRtmIdleTimer();
    if (!this._rtm) {
      return;
    }
    this._rtmIdleTimer = setTimeout(() => {
      this._rtmIdleTimer = null;
      void this._logoutRtm();
    }, RTM_IDLE_LOGOUT_MS);
  }

  async _logoutRtm() {
    this._clearRtmIdleTimer();
    const rtm = this._rtm;
    this._rtm = null;
    this._rtmUserId = "";
    await settleWithTimeout(rtm?.logout(), 2_000);
  }

  _startHls(Hls, videoPlaylistUrl, audioPlaylistUrl, runId) {
    if (!Hls?.isSupported?.()) {
      throw new Error("This browser does not support HLS playback");
    }
    const video = document.createElement("video");
    video.autoplay = true;
    video.controls = false;
    video.muted = true;
    video.playsInline = true;
    video.setAttribute("playsinline", "");
    const audio = document.createElement("audio");
    audio.autoplay = true;
    audio.muted = this._muted;
    audio.hidden = true;
    this._player.replaceChildren(video, audio);
    this._video = video;
    this._audio = audio;
    this._videoReady = false;
    this._audioReady = false;
    this._playStarted = false;
    const hlsConfig = {
      enableWorker: true,
      lowLatencyMode: true,
      liveSyncDurationCount: 2,
      maxBufferLength: 5,
      xhrSetup: (xhr) => {
        const headers = mediaAuthorizationHeaders(this._hass);
        if (headers.Authorization) {
          xhr.setRequestHeader("Authorization", headers.Authorization);
        }
      },
    };
    const hls = new Hls(hlsConfig);
    const audioHls = new Hls(hlsConfig);
    this._hls = hls;
    this._audioHls = audioHls;
    hls.on(Hls.Events.MANIFEST_PARSED, () => {
      if (runId !== this._runId) {
        return;
      }
      this._videoReady = true;
      void this._playWhenReady(runId, video, audio);
    });
    hls.on(Hls.Events.ERROR, (_event, data) => {
      if (runId !== this._runId || !data?.fatal) {
        return;
      }
      this._status = "error";
      this._error = `HLS ${data.type || "error"}: ${data.details || "playback failed"}`;
      this._render();
      void this._cleanupMedia(runId, { sendStop: true });
    });
    audioHls.on(Hls.Events.MANIFEST_PARSED, () => {
      if (runId !== this._runId) {
        return;
      }
      this._audioReady = true;
      void this._playWhenReady(runId, video, audio);
    });
    audioHls.on(Hls.Events.ERROR, (_event, data) => {
      if (runId !== this._runId || !data?.fatal) {
        return;
      }
      this._status = "error";
      this._error = `HLS audio ${data.details || "playback failed"}`;
      this._render();
      void this._cleanupMedia(runId, { sendStop: true });
    });
    hls.loadSource(videoPlaylistUrl);
    hls.attachMedia(video);
    audioHls.loadSource(audioPlaylistUrl);
    audioHls.attachMedia(audio);
  }

  async _playWhenReady(runId, video, audio) {
    if (
      runId !== this._runId ||
      this._playStarted ||
      !this._videoReady ||
      !this._audioReady
    ) {
      return;
    }
    this._playStarted = true;
    try {
      await Promise.all([video.play(), audio.play()]);
      if (runId === this._runId) {
        this._status = "playing";
        this._error = "";
        this._render();
      }
    } catch (error) {
      if (runId === this._runId) {
        this._status = "error";
        this._error = String(error?.message || error);
        this._render();
        void this._cleanupMedia(runId, { sendStop: true });
      }
    }
  }

  _toggleMute() {
    this._muted = !this._muted;
    if (this._audio) {
      this._audio.muted = this._muted;
    }
    this._render();
  }

  async _stop({ logoutRtm = false } = {}) {
    this._runId += 1;
    this._busy = false;
    await this._cleanupMedia(this._runId, { sendStop: true, logoutRtm });
    this._status = "stopped";
    this._error = "";
    this._render();
  }

  async _cleanupMedia(
    runId,
    { sendStop = false, logoutRtm = false } = {},
  ) {
    if (this._hiddenTimer) {
      clearTimeout(this._hiddenTimer);
      this._hiddenTimer = null;
    }
    if (this._sessionTimer) {
      clearTimeout(this._sessionTimer);
      this._sessionTimer = null;
    }
    if (this._authTimer) {
      clearInterval(this._authTimer);
      this._authTimer = null;
    }
    this._stopHeartbeat();
    const sessionId = this._sessionId;
    const access = this._access;
    const rtm = this._rtm;
    const hls = this._hls;
    const audioHls = this._audioHls;
    const video = this._video;
    const audio = this._audio;
    this._hls = null;
    this._audioHls = null;
    this._video = null;
    this._audio = null;
    this._videoReady = false;
    this._audioReady = false;
    this._playStarted = false;
    this._sessionId = "";
    this._access = null;
    try {
      hls?.destroy();
      audioHls?.destroy();
    } catch {
      // HLS.js may already be stopped by a failed network request.
    }
    try {
      video?.pause();
      video?.removeAttribute("src");
      video?.load();
      audio?.pause();
      audio?.removeAttribute("src");
    } catch {
      // The element may already have been detached.
    }
    this._player.replaceChildren();
    if (sendStop && rtm && access) {
      await settleWithTimeout(
        rtm.publish(
          String(access.device_rtm_user_id),
          stopLiveCommand(),
          { channelType: "USER" },
        ),
        1_500,
      );
    }
    if (logoutRtm) {
      await this._logoutRtm();
    } else if (rtm) {
      this._scheduleRtmLogout();
    }
    if (sendStop && sessionId && this._hass) {
      try {
        await stopCameraStream(this._hass, sessionId);
      } catch {
        // The sidecar session may already have expired.
      }
    }
    if (runId !== this._runId) {
      return;
    }
  }
}

if (!customElements.get("petkit-schedule-card")) {
  customElements.define("petkit-schedule-card", PetkitScheduleCard);
}
if (!customElements.get("petkit-camera-card")) {
  customElements.define("petkit-camera-card", PetkitCameraCard);
}
window.customCards = window.customCards || [];
window.customCards.push(
  {
    type: "petkit-schedule-card",
    name: "PETKIT Schedule",
    description: "Edit the complete seven-day D4sh feeding schedule.",
  },
  {
    type: "petkit-camera-card",
    name: "PETKIT Camera",
    description: "Play PETKIT D4sh live video and audio on demand.",
  },
);

globalThis.PetkitCardInternals = {
  canReuseRtm,
  settleWithTimeout,
  mediaAuthorizationHeaders,
  stopUnusedCameraSession,
  refreshMediaAuthorization,
  waitForMediaPlaylist,
  requestCameraStream,
  stopCameraStream,
  startLiveCommand,
  stopLiveCommand,
  liveHeartbeatCommand,
  secondsToInputValue,
  inputValueToSeconds,
  validateScheduleDraft,
  scheduleFromState,
};
