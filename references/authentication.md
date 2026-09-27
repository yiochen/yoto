# Yoto authentication

## One-time Yoto developer setup

1. Open [Yoto Developers: Start Here](https://yoto.dev/get-started/start-here/) and follow its developer-portal link.
2. Create a **public** OAuth application. This skill uses Authorization Code with PKCE and therefore does not need a client secret.
3. For authorization from a phone, register this redirect URI exactly: `https://yiochen.github.io/yoto-auth-callback/`.
4. Optionally register `http://127.0.0.1:8787/callback` for same-desktop login.
5. Enable the capabilities in the access matrix below. In the developer portal, `family:library:manage` can stand in for `family:library:view`, and `user:content:manage` includes both `user:content:view` and `user:icons:manage`. The authorization request still names every child scope explicitly because Yoto's live endpoints can require it to appear literally in the token.
6. Copy the client ID, then run:

```bash
uv run --project "$YOTO_SKILL_DIR" python "$YOTO_SKILL_DIR/scripts/yoto.py" --json auth configure --client-id CLIENT_ID
uv run --project "$YOTO_SKILL_DIR" python "$YOTO_SKILL_DIR/scripts/yoto.py" --json auth begin
```

In Windows PowerShell, set `$YOTO_SKILL_DIR` to the skill folder and use `uv run --project $YOTO_SKILL_DIR python "$YOTO_SKILL_DIR/scripts/yoto.py" --json auth ...`. Run this under the same Windows account that will later use the playlist commands.

Open the returned `authorization_url` on the phone. After approving access, the callback page displays a JSON object containing `code` and `state`. Copy the whole object and paste it into the conversation. Pass it to the completion command over standard input:

```bash
uv run --project "$YOTO_SKILL_DIR" python "$YOTO_SKILL_DIR/scripts/yoto.py" --json auth complete
```

Do not put that JSON in a command-line argument. Launch the command in a PTY, send the JSON to standard input, then send EOF (`Ctrl-D` on macOS; `Ctrl-Z` then Enter on Windows). The authorization code is short-lived and single-use. `auth begin` treats its locally stored PKCE verifier and state as invalid after ten minutes; expired data is removed on a completion attempt, logout, or replacement by a new attempt. Restart the flow if completion reports expiry.

For a same-desktop flow, run `auth login`; it opens the browser and receives the callback on loopback without copy/paste.

Yoto may restrict a new app's available scopes in its developer portal. If consent reports `invalid_scope`, compare its settings with the matrix below and enable each capability directly or through the documented parent scope.

## Required access

| Requested scope | Used for | Developer-portal grant |
| --- | --- | --- |
| `family:library:view` | Open full card details from the user's family library | Enable it directly, or enable `family:library:manage`, which includes it |
| `user:content:view` | List the user's MYO cards and read chapters/songs | Enable it directly, or enable `user:content:manage`, which includes it |
| `user:content:manage` | Create and update MYO playlists; add, rename, reorder, remove, and re-icon songs | Enable `user:content:manage` |
| `user:icons:manage` | View and upload the custom 16×16 icons generated for songs | Enable it directly, or enable `user:content:manage`, which includes it |
| `offline_access` | Obtain and rotate refresh tokens for later sessions | Enable `offline_access` |

The minimal practical portal configuration is therefore:

- `family:library:view` **or** `family:library:manage`
- `user:content:manage`
- `offline_access`

The skill does not need `family:library:manage` itself and never requests it. It also does not request `family:view`, device scopes, `profile`, or email access. A new authorization is required after changing scopes; refreshing an older token does not add permissions.

Run `auth status` to see the token's safe `scopes` and `missing_scopes` lists. These fields never contain access or refresh tokens.

## Local security model

- Client ID: `~/Library/Application Support/Yoto Skill/config.json`, mode `0600`.
- OAuth token document: macOS Keychain, service `com.openai.codex.skill.yoto`, account equal to the configured client ID.
- Pending phone authorization: the same Keychain service, a separate account suffixed with `:pending-login`, automatically removed after successful completion or detected expiry.
- Generated icon sources and previews: `~/Library/Application Support/Yoto Skill/generated-icons/`.
- Per-card advisory locks: `~/Library/Application Support/Yoto Skill/locks/`.

On Windows, the client ID, generated icons, and card locks are under `%LOCALAPPDATA%\Yoto Skill`. OAuth tokens and pending PKCE data are encrypted with current-user Windows Data Protection (DPAPI) before storage in its `credentials` directory. They can be decrypted only in the same Windows user context. Windows Hello may unlock that session, but reading a token does not trigger a separate Hello prompt. Keep the directory local rather than in a synchronized folder.

The CLI refreshes access shortly before expiry. Yoto refresh tokens are single-use, so it writes every rotated token to the platform's protected storage immediately. On an API `401`, it refreshes and retries the request once only.

Use `auth logout` to remove the protected token. It intentionally keeps the public client ID configuration.

## OAuth endpoints and parameters

- Authorization: `GET https://login.yotoplay.com/authorize`
- Token exchange and refresh: `POST https://login.yotoplay.com/oauth/token`
- Audience: `https://api.yotoplay.com`
- Scopes: `family:library:view user:content:view user:content:manage user:icons:manage offline_access`
- PKCE method: `S256`
- State: random and checked with a constant-time comparison before token exchange
- Redirect URI: the exact URI used by `auth begin` is retained locally and reused during token exchange

See Yoto's current [authentication overview](https://yoto.dev/authentication/auth/), [browser/PKCE guide](https://yoto.dev/authentication/browser-auth/), and [scope list](https://yoto.dev/authentication/scopes/).
