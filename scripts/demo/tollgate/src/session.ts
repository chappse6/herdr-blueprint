import type { NextFunction, Request, Response } from "express";
import { randomBytes } from "node:crypto";
import { redis } from "./store.js";

declare module "express-serve-static-core" {
  interface Request {
    session?: { userId: string };
  }
}

const COOKIE = "tg_session";
const TTL_SECONDS = 8 * 3600;

// Loads the signed-in user from the session cookie, if there is one.
export async function session(req: Request, _res: Response, next: NextFunction) {
  const id = req.headers.cookie?.match(/(?:^|;\s*)tg_session=([^;]+)/)?.[1];
  const userId = id ? await redis.get(`session:${id}`) : null;
  if (userId) req.session = { userId };
  next();
}

export async function startSession(res: Response, userId: string): Promise<void> {
  const id = randomBytes(32).toString("base64url");
  await redis.set(`session:${id}`, userId, "EX", TTL_SECONDS);
  res.cookie(COOKIE, id, { httpOnly: true, secure: true, sameSite: "lax", maxAge: TTL_SECONDS * 1000 });
}
