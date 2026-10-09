import type { Request, Response } from "express";
import { verify } from "@node-rs/argon2";
import { startSession } from "../session.js";
import { db } from "../store.js";

// Only local paths: never bounce to another site after sign-in.
function safeReturn(path: unknown): string {
  return typeof path === "string" && path.startsWith("/") && !path.startsWith("//") ? path : "/";
}

export function loginPage(req: Request, res: Response) {
  const back = encodeURIComponent(safeReturn(req.query.return_to));
  res.send(`<form method="post">
    <input name="email" type="email" autocomplete="username">
    <input name="password" type="password" autocomplete="current-password">
    <input name="return_to" type="hidden" value="${back}">
    <button>Sign in</button>
  </form>`);
}

export async function login(req: Request, res: Response) {
  const { email, password, return_to } = req.body as Record<string, string>;
  const { rows } = await db.query("SELECT id, password_hash FROM users WHERE email = $1", [email]);
  const ok = rows.length === 1 && (await verify(rows[0].password_hash, password));
  if (!ok) return res.status(401).send("Wrong email or password");

  await startSession(res, rows[0].id);
  res.redirect(safeReturn(decodeURIComponent(return_to ?? "/")));
}
