import { describe, expect, it } from "vitest";

import { ApiError, buildUrl, parseError } from "../api/client";

describe("API client", () => {
  it("keeps the server's code and field errors", async () => {
    const response = new Response(
      JSON.stringify({ detail: "2 fields need attention.", code: "INVALID_PERSONNEL", errors: [{ field: "email", code: "INVALID_EMAIL", message: "Email Address is not a valid email address." }] }),
      { status: 422 },
    );
    const error = await parseError(response);
    expect(error).toBeInstanceOf(ApiError);
    expect(error.status).toBe(422);
    expect(error.code).toBe("INVALID_PERSONNEL");
    expect(error.errors[0].field).toBe("email");
  });

  it("summarizes request validation errors without values", async () => {
    const response = new Response(JSON.stringify({ detail: [{ loc: ["body", "full_name"], msg: "Field required" }] }), { status: 422 });
    const error = await parseError(response);
    expect(error.message).toBe("Some fields are missing or not valid.");
    expect(error.errors).toEqual([{ field: "full_name", message: "Field required" }]);
  });

  it("drops empty query values", () => {
    expect(buildUrl("/personnel", { q: "ali", category_id: "", status: undefined, limit: 25 })).toBe("/api/v1/personnel?q=ali&limit=25");
  });
});
