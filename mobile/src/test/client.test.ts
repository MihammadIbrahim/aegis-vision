import { ApiError, apiRequest, configureApiClient } from "../api/client";
import { fail, mockApi, NETWORK, ok } from "./utils";

describe("API client", () => {
  const onSessionEnd = jest.fn();
  beforeEach(() => {
    onSessionEnd.mockReset();
    configureApiClient({ getToken: () => "tok", onSessionEnd });
  });

  it("sends the bearer token and JSON, and never organization or campus IDs", async () => {
    const api = mockApi({ "POST /alerts/a1/acknowledge": ok({ alert_id: "a1", outcome: "DONE", status: "ACKNOWLEDGED" }) });
    await apiRequest("POST", "/alerts/a1/acknowledge", { body: { client_action_id: "c1" } });
    const call = api.calls[0];
    expect(call.headers.Authorization).toBe("Bearer tok");
    expect(call.body).toEqual({ client_action_id: "c1" });
    expect(JSON.stringify(call)).not.toMatch(/organization_id|campus_id/);
  });

  it("ends the session on 401 ASSIGNMENT_CHANGED from any signed-in request", async () => {
    mockApi({ "GET /alerts": fail(401, "Your operational assignment changed. Please sign in again.", "ASSIGNMENT_CHANGED") });
    await expect(apiRequest("GET", "/alerts")).rejects.toMatchObject({ status: 401, code: "ASSIGNMENT_CHANGED" });
    expect(onSessionEnd).toHaveBeenCalledWith({
      code: "ASSIGNMENT_CHANGED",
      message: "Your operational assignment changed. Please sign in again.",
    });
  });

  it("ends the session on 403 ORGANIZATION_INACTIVE but not on other 403s", async () => {
    mockApi({
      "GET /guard/dashboard": fail(403, "This organization is not active.", "ORGANIZATION_INACTIVE"),
      "GET /alerts/x/snapshot": fail(403, "Only Admins and Supervisors can use this.", "STAFF_ONLY"),
    });
    await expect(apiRequest("GET", "/guard/dashboard")).rejects.toBeInstanceOf(ApiError);
    expect(onSessionEnd).toHaveBeenCalledTimes(1);
    await expect(apiRequest("GET", "/alerts/x/snapshot")).rejects.toMatchObject({ code: "STAFF_ONLY" });
    expect(onSessionEnd).toHaveBeenCalledTimes(1);
  });

  it("does not treat a public sign-in 401 as a session end", async () => {
    mockApi({ "POST /auth/guard/login": fail(401, "Wrong details.", "INVALID_CREDENTIALS") });
    await expect(apiRequest("POST", "/auth/guard/login", { auth: false, body: {} })).rejects.toMatchObject({
      code: "INVALID_CREDENTIALS",
    });
    expect(onSessionEnd).not.toHaveBeenCalled();
  });

  it("reports network failures as status 0 and keeps lockout details", async () => {
    mockApi({ "GET /alerts": NETWORK, "POST /auth/guard/login": fail(429, "Locked.", "ACCOUNT_LOCKED", { retry_after_seconds: 840 }) });
    const network = await apiRequest("GET", "/alerts").catch((e: ApiError) => e);
    expect(network).toMatchObject({ status: 0, code: "NETWORK_ERROR", isNetworkError: true });
    const locked = await apiRequest("POST", "/auth/guard/login", { auth: false, body: {} }).catch((e: ApiError) => e);
    expect(locked).toMatchObject({ status: 429, retryAfterSeconds: 840 });
  });

  it("joins FastAPI validation messages without echoing values", async () => {
    mockApi({ "POST /x": { status: 422, body: { detail: [{ loc: ["body", "otp"], msg: "String should match pattern", type: "x" }] } } });
    await expect(apiRequest("POST", "/x", { body: {} })).rejects.toThrow("String should match pattern");
  });
});
