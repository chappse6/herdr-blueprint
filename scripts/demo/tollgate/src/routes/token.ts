import type { Request, Response } from "express";
import { verifierMatches } from "../pkce.js";
import { takeCode } from "../store.js";
import { accessToken, refreshToken, rotate } from "../tokens.js";

// POST /token  grant_type=authorization_code | refresh_token
export async function token(req: Request, res: Response) {
  const body = req.body as Record<string, string>;

  if (body.grant_type === "authorization_code") {
    const pending = await takeCode(body.code);
    const valid =
      pending &&
      pending.clientId === body.client_id &&
      pending.redirectUri === body.redirect_uri &&
      verifierMatches(body.code_verifier, pending.challenge);
    if (!valid) return res.status(400).json({ error: "invalid_grant" });

    return res.json({
      token_type: "Bearer",
      access_token: await accessToken(pending.userId, pending.clientId),
      refresh_token: await refreshToken(pending.userId),
      expires_in: 600,
    });
  }

  if (body.grant_type === "refresh_token") {
    const result = await rotate(body.refresh_token);
    if (result === "reused") return res.status(400).json({ error: "invalid_grant", error_description: "token reuse" });
    if (!result) return res.status(400).json({ error: "invalid_grant" });

    return res.json({
      token_type: "Bearer",
      access_token: await accessToken(result.userId, body.client_id),
      refresh_token: result.next,
      expires_in: 600,
    });
  }

  res.status(400).json({ error: "unsupported_grant_type" });
}
