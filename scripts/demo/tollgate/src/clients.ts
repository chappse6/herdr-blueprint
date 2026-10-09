import { db } from "./store.js";

export interface Client {
  id: string;
  redirectUris: string[];
}

export async function findClient(id: string): Promise<Client | null> {
  const { rows } = await db.query("SELECT id, redirect_uris FROM clients WHERE id = $1", [id]);
  if (rows.length === 0) return null;
  return { id: rows[0].id, redirectUris: rows[0].redirect_uris };
}

// Exact match only: no wildcards, no prefix matching.
export function allowsRedirect(client: Client, uri: string): boolean {
  return client.redirectUris.includes(uri);
}
