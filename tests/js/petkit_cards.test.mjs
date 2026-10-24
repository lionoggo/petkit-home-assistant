import assert from "node:assert/strict";
import test from "node:test";

globalThis.HTMLElement = class {};
globalThis.window = globalThis;
globalThis.document = {
  addEventListener() {},
  removeEventListener() {},
  head: { appendChild() {} },
};
globalThis.customElements = {
  values: new Map(),
  get(name) {
    return this.values.get(name);
  },
  define(name, value) {
    this.values.set(name, value);
  },
};

await import("../../custom_components/petkit/frontend/petkit-cards.js");
const internals = globalThis.PetkitCardInternals;

test("RTM commands use the vendor's isSD field", () => {
  assert.equal(
    internals.startLiveCommand(false),
    '{"cmd":"start_live","payload":{"isSD":0}}',
  );
  assert.equal(
    internals.liveHeartbeatCommand(true),
    '{"cmd":"live_heartbeat","payload":{"isSD":1}}',
  );
});

test("RTM sessions are reused for the same vendor user", () => {
  assert.equal(
    internals.canReuseRtm("app-user", { app_rtm_user_id: "app-user" }),
    true,
  );
  assert.equal(
    internals.canReuseRtm("app-user", { app_rtm_user_id: "other-user" }),
    false,
  );
  assert.equal(internals.canReuseRtm("", { app_rtm_user_id: "" }), false);
});

test("best-effort RTM operations cannot block cleanup", async () => {
  const started = Date.now();
  await internals.settleWithTimeout(new Promise(() => {}), 1);
  assert.ok(Date.now() - started < 500);
});

test("media proxy requests use the current Home Assistant token", () => {
  assert.deepEqual(
    internals.mediaAuthorizationHeaders({
      auth: { data: { access_token: "ha-token" } },
    }),
    { Authorization: "Bearer ha-token" },
  );
  assert.deepEqual(internals.mediaAuthorizationHeaders({}), {});
});

test("expired Home Assistant tokens are refreshed before media requests", async () => {
  const auth = {
    expired: true,
    data: { access_token: "old-token", refresh_token: "refresh-token" },
    async refreshAccessToken() {
      this.expired = false;
      this.data.access_token = "new-token";
    },
  };

  await internals.refreshMediaAuthorization({ auth });

  assert.equal(auth.expired, false);
  assert.deepEqual(internals.mediaAuthorizationHeaders({ auth }), {
    Authorization: "Bearer new-token",
  });
});

test("Home Assistant tokens nearing expiry are refreshed early", async () => {
  const auth = {
    expired: false,
    data: {
      access_token: "old-token",
      refresh_token: "refresh-token",
      expires: Date.now() + 60_000,
    },
    async refreshAccessToken() {
      this.data.access_token = "new-token";
      this.data.expires = Date.now() + 1_800_000;
    },
  };

  await internals.refreshMediaAuthorization({ auth });

  assert.equal(auth.data.access_token, "new-token");
});

test("media playlists are polled until the sidecar is ready", async () => {
  let attempts = 0;
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async () => {
    attempts += 1;
    return {
      ok: attempts >= 3,
      async text() {
        return attempts >= 3 ? "#EXTM3U\n" : "";
      },
    };
  };
  try {
    await internals.waitForMediaPlaylist(
      "/api/petkit/media/session/video.m3u8",
      { auth: { data: { access_token: "ha-token" } } },
      { timeoutMs: 1_000, intervalMs: 1 },
    );
  } finally {
    globalThis.fetch = originalFetch;
  }
  assert.equal(attempts, 3);
});

test("camera stream requests a native service response", async () => {
  const calls = [];
  const response = await internals.requestCameraStream(
    {
      async callService(...args) {
        calls.push(args);
        return {
          response: {
            session_id: "session",
            video_playlist_url: "/api/petkit/media/session/video.m3u8",
            audio_playlist_url: "/api/petkit/media/session/audio.m3u8",
            rtm_token: "rtm-token",
            app_rtm_user_id: "app-user",
            device_rtm_user_id: "device-user",
          },
        };
      },
    },
    { device_id: "453354", quality: 2 },
  );
  assert.equal(response.response.session_id, "session");
  assert.deepEqual(calls[0].slice(0, 3), [
    "petkit",
    "start_camera_stream",
    { device_id: "453354", quality: 2 },
  ]);
  assert.equal(calls[0][5], true);
});

test("camera stop sends the opaque sidecar session", async () => {
  let call = null;
  await internals.stopCameraStream(
    {
      async callService(...args) {
        call = args;
      },
    },
    "session-123",
  );
  assert.deepEqual(call.slice(0, 3), [
    "petkit",
    "stop_camera_stream",
    { session_id: "session-123" },
  ]);
});

test("stale camera responses stop their newly created sidecar session", async () => {
  let call = null;
  await internals.stopUnusedCameraSession(
    {
      async callService(...args) {
        call = args;
      },
    },
    { session_id: "stale-session" },
  );
  assert.deepEqual(call.slice(0, 3), [
    "petkit",
    "stop_camera_stream",
    { session_id: "stale-session" },
  ]);
});

test("a stopped connection cannot clear a replacement camera session", async () => {
  const Card = globalThis.customElements.get("petkit-camera-card");
  const card = Object.create(Card.prototype);
  const stoppedSessions = [];
  card._hass = {
    async callService(_domain, service, data) {
      if (service === "start_camera_stream") {
        return {
          session_id: "old-session",
          video_playlist_url: "/video.m3u8",
          audio_playlist_url: "/audio.m3u8",
          rtm_token: "rtm-token",
          app_rtm_user_id: "app-user",
          device_rtm_user_id: "device-user",
        };
      }
      stoppedSessions.push(data.session_id);
      return undefined;
    },
  };
  card._config = { device_id: "device", quality: "hd" };
  card._runId = 0;
  card._busy = false;
  card._status = "idle";
  card._error = "";
  card._sessionId = "";
  card._access = null;
  card._render = () => {};
  card._startRtm = async () => {
    card._runId += 1;
    card._sessionId = "replacement-session";
    card._access = { session_id: "replacement-session" };
    return false;
  };

  const originalHls = globalThis.Hls;
  const originalRtm = globalThis.AgoraRTM;
  globalThis.Hls = { isSupported: () => true };
  globalThis.AgoraRTM = {};
  try {
    await card._play();
  } finally {
    globalThis.Hls = originalHls;
    globalThis.AgoraRTM = originalRtm;
  }

  assert.equal(card._sessionId, "replacement-session");
  assert.deepEqual(card._access, { session_id: "replacement-session" });
  assert.deepEqual(stoppedSessions, ["old-session"]);
});

test("camera card renders when hass becomes available", () => {
  const Card = globalThis.customElements.get("petkit-camera-card");
  const descriptor = Object.getOwnPropertyDescriptor(Card.prototype, "hass");
  const card = Object.create(Card.prototype);
  let renders = 0;
  card._render = () => {
    renders += 1;
  };
  descriptor.set.call(card, { states: {} });
  assert.equal(renders, 1);
  assert.deepEqual(card._hass, { states: {} });
});

test("camera cleanup accepts the RTM logout option", () => {
  const Card = globalThis.customElements.get("petkit-camera-card");
  assert.match(Card.prototype._cleanupMedia.toString(), /logoutRtm = false/);
});

test("time conversion is minute accurate", () => {
  assert.equal(internals.secondsToInputValue(27000), "07:30");
  assert.equal(internals.inputValueToSeconds("18:00"), 64800);
});

test("schedule validation rejects duplicate times", () => {
  const days = [1, 2, 3, 4, 5, 6, 7].map((repeats) => ({
    repeats,
    suspended: 0,
    items: [{ time: 27000, amount1: 1, amount2: 0 }],
  }));
  days[0].items.push({ time: 27000, amount1: 1, amount2: 0 });
  assert.throws(() => internals.validateScheduleDraft(days), /unique/);
});
