import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("next/headers", () => ({
  cookies: async () => ({ toString: () => "session=x" }),
}));

import { getResearchCampaign, getResearchExperiment, getResearchOverview } from "./server-api";

afterEach(() => {
  vi.unstubAllGlobals();
});

function stubFetch(impl: (url: string) => Promise<Response>) {
  const fetchMock = vi.fn((url: string) => impl(url));
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

describe("research API reads", () => {
  it("returns data on success", async () => {
    stubFetch(async () => new Response(JSON.stringify({ counts: {} }), { status: 200 }));
    expect(await getResearchOverview()).toEqual({ status: "ok", data: { counts: {} } });
  });

  it("distinguishes a missing artifact from an unavailable API", async () => {
    stubFetch(async () => new Response("{}", { status: 404 }));
    expect(await getResearchCampaign("nope-v1")).toEqual({ status: "not_found" });
    stubFetch(async () => new Response("{}", { status: 500 }));
    expect(await getResearchCampaign("x-v1")).toEqual({ status: "error", message: "HTTP 500" });
    stubFetch(async () => {
      throw new Error("ECONNREFUSED");
    });
    expect(await getResearchOverview()).toEqual({
      status: "error",
      message: "research API unreachable",
    });
  });

  it("encodes identifiers so they cannot change the requested path", async () => {
    const fetchMock = stubFetch(async () => new Response("{}", { status: 404 }));
    await getResearchExperiment("../../api/me");
    expect(fetchMock.mock.calls[0][0]).toMatch(/\/api\/research\/experiments\/..%2F..%2Fapi%2Fme$/);
  });
});
