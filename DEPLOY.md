# Deploying to Production (GitHub → DigitalOcean App Platform)

This app is a **Chainlit** server. It needs a long-running process with WebSocket
support, so it runs as a container — **not** as a Vercel serverless function.

DigitalOcean App Platform connects directly to your GitHub repo, builds the
included `Dockerfile` on their servers, and **redeploys automatically every time
you push**. No Docker Hub and no local Docker build required.

---

## 1. Push to GitHub (safely)

Your secrets are protected — `.gitignore` blocks `.env` and all `*.json`
(including `google_credentials.json`), so they will **not** be uploaded.

Make sure these ARE in the repo (they're needed for the app to work):
`Dockerfile`, `.dockerignore`, `.chainlit/config.toml`, `public/custom.css`,
`chainlit.md`, all `.py` files, `requirements.txt`.

```powershell
git add -A
git commit -m "Production: Dockerfile + container config for DigitalOcean"
git push origin production-1
```

> Quick sanity check before pushing — confirm no secrets are staged:
> `git status` should NOT list `.env` or `google_credentials.json`.

---

## 2. The environment variables

Nothing is baked into the image. Every secret and setting is entered in the
**DigitalOcean dashboard**. The Google service-account JSON goes in as **one
env var** (base64), not as a file.

| Variable                  | Required?              | Example / Notes                          |
|---------------------------|------------------------|------------------------------------------|
| `MODEL_PROVIDER`          | yes                    | `groq` or `openai`                       |
| `GROQ_API_KEY`            | if provider = groq     | `gsk_...`                                |
| `GROQ_MODEL`              | optional               | `llama-3.3-70b-versatile` (default)      |
| `OPENAI_API_KEY`          | if provider = openai   | `sk-...`                                 |
| `OPENAI_MODEL`            | if provider = openai   | `gpt-4o`                                 |
| `SHEET_URL`               | yes                    | full Google Sheet URL                    |
| `SHEET_TAB`               | yes                    | `New Vision`                             |
| `GOOGLE_CREDENTIALS_JSON` | yes                    | base64 of `google_credentials.json`      |

> Local dev is unchanged: keep using `.env` with
> `GOOGLE_CREDENTIALS_PATH=google_credentials.json`. The code prefers
> `GOOGLE_CREDENTIALS_JSON` only when it's set (i.e. in production).

### Generate `GOOGLE_CREDENTIALS_JSON` (Windows PowerShell)

Run this in the project folder:

```powershell
[Convert]::ToBase64String([IO.File]::ReadAllBytes("google_credentials.json")) | Set-Clipboard
```

It copies a single-line base64 string to your clipboard. Paste it as the value
of `GOOGLE_CREDENTIALS_JSON` in DigitalOcean and mark it **Encrypted**.

---

## 3. Create the app on DigitalOcean

1. **Apps → Create App → GitHub.** Authorize DigitalOcean to access your repo.
2. Select the repository and the **`production-1`** branch.
3. DigitalOcean auto-detects the `Dockerfile` and sets the source type to
   **Dockerfile** (no buildpack guessing).
4. In the component settings, set **HTTP Port = `8080`**.
   (App Platform injects `$PORT=8080`; the container reads it automatically.)
5. Open **Environment Variables** and add every row from the table above.
   Mark the API keys and `GOOGLE_CREDENTIALS_JSON` as **Encrypted**.
6. Choose the smallest plan to start (Basic, ~$5–12/mo is plenty).
7. **Create Resources / Deploy.**

You'll get an HTTPS `*.ondigitalocean.app` URL within a few minutes. WebSockets
and SSL work out of the box — no nginx config needed.

---

## 4. Automatic redeploys

Leave **Autodeploy** on (default). From now on, every `git push` to
`production-1` rebuilds and redeploys the app. No further manual steps.

---

## 5. Attach your domain

In the app's **Settings → Domains**, add your domain and follow the DNS
instructions (CNAME for a subdomain, or A record for the apex). SSL is issued
automatically. Once DNS propagates, share that URL — anyone can use it.

---

## Notes

- **Open access:** Chainlit has no login by default, so anyone with the URL can
  chat. That matches your goal. To restrict later, Chainlit supports
  password / OAuth auth.
- **CORS** is `["*"]` in `.chainlit/config.toml` — fine for a public app.
- **Data is read-only & cached** 30 min from the Google Sheet; users can type
  `refresh` to pull the latest. Nothing is written back to the sheet.
- **Groq free tier** has a tokens-per-minute limit — for real traffic, consider
  `MODEL_PROVIDER=openai` or a paid Groq tier.
- You do **not** need Docker installed locally for this flow — DigitalOcean
  builds the image. You can quit Docker Desktop.
