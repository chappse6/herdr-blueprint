import { Redis } from "ioredis";
import pg from "pg";

export const db = new pg.Pool({ connectionString: process.env.DATABASE_URL });
export const redis = new Redis(process.env.REDIS_URL ?? "redis://localhost:6379");

export interface PendingCode {
  clientId: string;
  userId: string;
  redirectUri: string;
  challenge: string;
}

// Codes live 60 seconds and can be redeemed once: GETDEL reads and removes.
export async function saveCode(code: string, pending: PendingCode): Promise<void> {
  await redis.set(`code:${code}`, JSON.stringify(pending), "EX", 60);
}

export async function takeCode(code: string): Promise<PendingCode | null> {
  const raw = await redis.getdel(`code:${code}`);
  return raw ? (JSON.parse(raw) as PendingCode) : null;
}
