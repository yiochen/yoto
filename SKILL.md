---
name: yoto
description: "Manage a user's Yoto Make Your Own (MYO) playlists: authenticate, list or inspect cards, create playlists, manage cover art, and add, rename, reorder, remove, or re-icon local MP3 songs. Use for Yoto cards, MYO playlists, MP3 uploads, card covers, or Yoto Player icons. Excludes purchased-card editing, physical card linking, device control, and non-MP3 media."
---

# Yoto

Manage Yoto MYO playlists through the official Yoto API. Use the bundled Python CLI; do not install or invoke `yoto-cli`.

## Run the CLI

Treat the directory containing this file as `YOTO_SKILL_DIR`. Run commands in JSON mode:

```bash
uv run --project "$YOTO_SKILL_DIR" python "$YOTO_SKILL_DIR/scripts/yoto.py" --json <category> <command> ...
```

Responses use one of these envelopes:

```json
{"ok": true, "data": {}, "warnings": []}
{"ok": false, "error": {"code": "...", "message": "...", "details": {}}}
```

Report useful results in natural language. Never print access or refresh tokens.

## Authenticate

If a command returns `not_configured` or `not_authenticated`, read [authentication.md](references/authentication.md) and guide the user through the missing step. The skill requests only these capabilities:

- `family:library:view` to access card details from the family library. The developer portal may grant `family:library:manage` instead because it includes view, but the skill does not request or use family-library mutation access.
- `user:content:view` to list MYO content and read its chapters.
- `user:content:manage` to create MYO playlists and add, rename, reorder, remove, or re-icon their songs.
- `user:icons:manage` to upload generated custom icons.
- `offline_access` to refresh the session without repeated login.

Request every scope above explicitly. Do not rely on Yoto's documented inheritance from a `manage` scope: the live API has rejected tokens where a required child `view` scope did not appear literally. Do not request device, profile, email, or family-management access.

Use `auth status` to compare `scopes` with `missing_scopes`. After any portal or requested-scope change, complete a new authorization; refresh tokens cannot gain scopes. If a protected endpoint returns `403`, report the endpoint's required scopes and reauthorize instead of retrying it.

Prefer the phone-friendly two-step flow:

```bash
# One-time setup
... --json auth configure --client-id CLIENT_ID

# Start phone authorization and return a URL
... --json auth begin

# After the user copies the callback JSON, pass it on standard input
... --json auth complete

# Diagnostics
... --json auth status
... --json auth logout
```

Give the returned `authorization_url` to the user. Ask them to open it on their phone, authorize Yoto, tap **Copy authorization result**, and paste the complete JSON object back into the conversation. Then launch `auth complete` in a PTY, send that exact JSON to its standard input, and send EOF (`Ctrl-D`) so the command can proceed. Never put the authorization code in a command-line argument, shell history, or file. After completion succeeds, continue the original card operation.

The phone flow treats the PKCE verifier and OAuth state in macOS Keychain as invalid after ten minutes; expired data is deleted when completion is attempted, logout runs, or a new attempt replaces it. The public callback page receives only the one-time code and state. For a same-desktop flow, `auth login` remains available and listens only on `127.0.0.1:8787`.

Do not request or store a client secret. Tokens and pending PKCE data belong in macOS Keychain.

## Inspect and list

```bash
... --json cards list
... --json cards show --card-id CARD_ID
... --json media inspect --file /absolute/path/song.mp3
```

Use `song_key`, not a visible position, for subsequent song mutations. Positions are one-based. A row with `manageable: false` is a multi-track chapter: explain that this v1 can list it but intentionally refuses to mutate it.

## Create a card

```bash
... --json cards create --title "Bedtime" --description "Quiet songs"
```

This creates MYO playlist content. It does not link a physical MYO card; the user can link it in the Yoto app.

## Set a playlist cover

Yoto centre-crops uploaded covers to a 638 × 1011 portrait. Find the uncropped source for the exact book, language, volume, and edition; prefer matching art embedded in an MP3 or the publisher's original image. If the current Yoto artwork has already lost text or artwork at the sides, do not reuse that cropped image. Search for the complete source and inspect its edges before preparing it.

Extract embedded art when appropriate, then compose a portrait canvas that contains the **entire** source image with safe margins:

```bash
... --json media cover --file /absolute/path/first-track.mp3
... --json media prepare-cover --file /absolute/path/extracted-cover.jpg
```

For a direct public HTTPS source image, prepare it locally in the same way:

```bash
... --json media prepare-cover --image-url 'https://publisher.example/uncropped-cover.jpg'
```

When one book spans several playlists, prepare a distinct cover for each playlist. Add `--part-number 1 --part-total 3`, then `2/3` and `3/3`; the corner badge shows `1/3`, etc. The fraction counts playlist parts of **one book**, separately from its tome number. Optional `--header`, `--title`, `--subtitle`, `--background`, and `--foreground` place a legible caption in the margins when the source text is too small at card size. Keep these captions accurate to the source and the playlist. For example:

```bash
... --json media prepare-cover --file /absolute/path/full-cover.jpg --part-number 1 --part-total 3 --header "À la croisée des mondes" --title "Les Royaumes du Nord" --subtitle "Chapitres 1–8" --background "#152744"
```

Inspect each returned `preview` at 160 × 254 pixels. Check that the full title and side artwork remain visible and that each part badge is legible. Only then upload the returned `cover` path:

```bash
... --json cards set-cover --card-id CARD_ID --file /absolute/path/cover-638x1011.png
```

`cards set-cover` accepts only a prepared 638 × 1011 PNG, re-reads the complete card, and verifies the stored URL. It downloads Yoto's published image, checks its size and similarity, re-reads the card once more, and returns a `published.preview` path. View that actual published preview before reporting success. If verification fails during another edit, the command waits for a stable card and retries once from its latest document. If it still returns `update_not_verified`, re-read and report the unresolved state without claiming the cover was saved. If no uncropped source can be verified, retain the existing artwork and explain why.

Read [cover-layout.md](references/cover-layout.md) for the complete visual checks when updating a library of audiobook covers.

## Add a song with a generated icon

1. Inspect the MP3. Use its title, artist, and album tags to understand the song; let an explicit user title or icon direction override metadata.
2. Invoke `$imagegen` to generate a square source image. Use this baseline prompt, substituting the song concept:

   > A single centered 16-bit pixel-art symbol representing [song concept], chunky readable silhouette, very high contrast, vivid limited palette, pure black background, no words, no letters, no logo, no border, no album-cover reproduction, no real performer likeness, square composition, designed to remain legible at 16 by 16 pixels.

3. Prepare the generated image. This copies the source into Yoto's local data directory, produces an exact 16×16 RGBA PNG, and makes a nearest-neighbor preview:

```bash
... --json icons prepare --input /absolute/path/generated.png --title "Song title"
```

4. Inspect the returned `preview` with the image-viewing tool. Check that it has one recognizable subject, a clean silhouette, black background, no text, and no tiny disconnected detail. Do not ask the user to approve it. If it is unreadable, regenerate once with a simpler subject and repeat preparation. If the second attempt is still unreadable, stop before changing the card and explain why.
5. Pass the prepared `icon` path to the add command:

```bash
... --json songs add --card-id CARD_ID --file /absolute/path/song.mp3 --icon-file /absolute/path/icon-16.png --title "Song title"
```

The upload may take several minutes while Yoto transcodes the MP3. The command hashes the audio for server-side deduplication and updates the latest card document only after both media uploads succeed.

## Change songs

Fetch `cards show` immediately before choosing a key. The commands fetch the latest card again under a local lock and preserve unknown server fields.

```bash
... --json songs rename --card-id CARD_ID --song-key SONG_KEY --title "New title"
... --json songs move --card-id CARD_ID --song-key SONG_KEY --position 1
... --json songs remove --card-id CARD_ID --song-key SONG_KEY --yes
```

Rename changes both chapter and track titles and does not regenerate the icon. Remove deletes only that chapter from the playlist; it does not delete uploaded media from Yoto's media store. Require the user's clear request before passing `--yes`.

## Replace an icon

Follow the same generate → prepare → inspect workflow used for a new song, then run:

```bash
... --json songs set-icon --card-id CARD_ID --song-key SONG_KEY --icon-file /absolute/path/icon-16.png
```

The same uploaded icon is applied to the chapter and its single track.

## Boundaries and recovery

- Support macOS, local MP3 files, and user-owned MYO playlists only.
- Never attempt to edit purchased content or link/unlink physical cards.
- Never hand-edit card JSON or call the API with ad hoc `curl`; use the script so refresh-token rotation and read-modify-write safety remain intact.
- If an API response no longer matches expectations, stop rather than inventing fields. Read [api.md](references/api.md), capture the structured error without credentials, and recommend checking Yoto's current API docs.
- A failed card save leaves already-uploaded audio/icon media unreferenced, but does not partially modify the playlist. Report that state clearly.
