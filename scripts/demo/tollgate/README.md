# Tollgate

A small OAuth 2.1 authorization server for internal apps. It signs people in,
issues short-lived access tokens and rotates refresh tokens.

- Authorization code flow with PKCE (S256 only)
- RS256 access tokens that live 10 minutes
- Refresh token rotation with reuse detection
- Redis for codes and token families, Postgres for users and clients

## Run

```bash
npm install
npm run dev   # http://localhost:4000
```

## Docs

- [Architecture](docs/architecture.md)
- [Login flow](docs/login-flow.md)
- [Refresh token rotation](docs/refresh-rotation.mmd)
