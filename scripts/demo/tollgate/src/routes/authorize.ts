import type { Request, Response } from "express";
import { randomBytes } from "node:crypto";
import { allowsRedirect, findClient } from "../clients.js";
import { saveCode } from "../store.js";

// GET /authorize?client_id&redirect_uri&state&code_challenge&code_challenge_method=S256
export async function authorize(req: Request, res: Response) {
  const { client_id, redirect_uri, state, code_challenge, code_challenge_method } = req.query as Record<string, string>;

  const client = await findClient(client_id);
  if (!client || !allowsRedirect(client, redirect_uri)) {
    // Never redirect to an unknown URI: show the error here instead.
    return res.status(400).send("Unknown client or redirect URI");
  }
  if (code_challenge_method !== "S256" || !code_challenge) {
    return res.redirect(`${redirect_uri}?error=invalid_request&state=${state}`);
  }

  const userId = req.session?.userId;
  if (!userId) return res.redirect(`/login?return_to=${encodeURIComponent(req.originalUrl)}`);

  const code = randomBytes(24).toString("base64url");
  await saveCode(code, { clientId: client.id, userId, redirectUri: redirect_uri, challenge: code_challenge });
  res.redirect(`${redirect_uri}?code=${code}&state=${state}`);
}
