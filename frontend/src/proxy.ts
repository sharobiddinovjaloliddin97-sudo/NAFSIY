import { createHash, timingSafeEqual } from "node:crypto";
import { NextRequest, NextResponse } from "next/server";

export function proxy(request: NextRequest) {
  const staging = process.env.APP_ENV === "staging" || (process.env.NODE_ENV === "production" && process.env.APP_ENV !== "development");
  const common = { "Cache-Control": "no-store", "X-Robots-Tag": "noindex, nofollow" };
  if (request.nextUrl.pathname === "/healthz") {
    return new NextResponse("ok", { headers: common });
  }
  if (staging) {
    const user = process.env.STAGING_ACCESS_USER;
    const password = process.env.STAGING_ACCESS_PASSWORD;
    if (!user || !password || password.length < 24 || !process.env.API_INTERNAL_URL) {
      return new NextResponse("Staging is not configured", { status: 503, headers: common });
    }
    const expected = "Basic " + Buffer.from(`${user}:${password}`).toString("base64");
    const hash = (value: string) => createHash("sha256").update(value).digest();
    if (!timingSafeEqual(hash(request.headers.get("authorization") || ""), hash(expected))) {
      return new NextResponse("Private staging", { status: 401,
        headers: { ...common, "WWW-Authenticate": 'Basic realm="NAFSIY private staging", charset="UTF-8"' } });
    }
  }
  if (request.nextUrl.pathname.startsWith("/api/")) {
    const target = process.env.API_INTERNAL_URL;
    if (!target) return new NextResponse("API is not configured", { status: 503, headers: common });
    const destination = new URL(request.nextUrl.pathname + request.nextUrl.search, target);
    const headers = new Headers(request.headers);
    headers.delete("authorization");
    // Do not forward attacker-supplied identity headers to the private backend.
    for (const name of ["forwarded", "x-forwarded-for", "x-real-ip"]) headers.delete(name);
    return NextResponse.rewrite(destination, { request: { headers } });
  }
  return NextResponse.next();
}
