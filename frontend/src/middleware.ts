import { NextRequest, NextResponse } from "next/server";

const PUBLIC_PATHS = ["/login", "/register"];

export function middleware(request: NextRequest) {
  const { pathname } = request.nextUrl;

  const isPublic = PUBLIC_PATHS.some((p) => pathname.startsWith(p));
  if (isPublic) return NextResponse.next();

  // Check for auth token in cookies (set on login) or allow through to let
  // client-side zustand/localStorage handle it.
  // For a lightweight check we look for the persisted zustand store key.
  const authStore = request.cookies.get("jarvis-auth");

  // If the cookie isn't present we still let the request through — the
  // client-side guard below will redirect if the token is missing.
  // This avoids SSR/localStorage mismatch issues.
  return NextResponse.next();
}

export const config = {
  matcher: ["/((?!_next|favicon.ico|api).*)"],
};
