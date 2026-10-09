import express from "express";
import { authorize } from "./routes/authorize.js";
import { login, loginPage } from "./routes/login.js";
import { token } from "./routes/token.js";
import { session } from "./session.js";
import { jwks } from "./tokens.js";

const app = express();
app.use(express.urlencoded({ extended: false }));
app.use(session);

app.get("/login", loginPage);
app.post("/login", login);
app.get("/authorize", authorize);
app.post("/token", token);
app.get("/.well-known/jwks.json", async (_req, res) => res.json(await jwks()));

app.listen(4000, () => console.log("Tollgate on http://localhost:4000"));
