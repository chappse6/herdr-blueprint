import { randomBytes } from "node:crypto";
import { SignJWT, exportJWK, importPKCS8 } from "jose";
import { redis } from "./store.js";

const ACCESS_TTL = "10m";
const FAMILY_TTL_SECONDS = 30 * 24 * 3600;
const key = importPKCS8(process.env.SIGNING_KEY!, "RS256");

export async function accessToken(userId: string, clientId: string): Promise<string> {
  return new SignJWT({ client_id: clientId })
    .setProtectedHeader({ alg: "RS256", kid: "tollgate-1" })
    .setSubject(userId)
    .setIssuedAt()
    .setExpirationTime(ACCESS_TTL)
    .sign(await key);
}

// Every refresh token belongs to a family. Only the newest one in a family
// is valid; using an older one means it leaked, so the family is revoked.
export async function refreshToken(userId: string, family = randomBytes(16).toString("hex")) {
  const value = randomBytes(32).toString("base64url");
  await redis.set(`family:${family}`, JSON.stringify({ userId, current: value }), "EX", FAMILY_TTL_SECONDS);
  await redis.set(`refresh:${value}`, family, "EX", FAMILY_TTL_SECONDS);
  return value;
}

export async function rotate(presented: string): Promise<{ userId: string; next: string } | "reused" | null> {
  const family = await redis.get(`refresh:${presented}`);
  if (!family) return null;
  const raw = await redis.get(`family:${family}`);
  if (!raw) return null;
  const { userId, current } = JSON.parse(raw);
  if (presented !== current) {
    await redis.del(`family:${family}`);
    return "reused";
  }
  return { userId, next: await refreshToken(userId, family) };
}

export async function jwks() {
  return { keys: [{ ...(await exportJWK(await key)), kid: "tollgate-1", alg: "RS256", use: "sig" }] };
}
