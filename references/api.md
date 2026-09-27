# Yoto API contract used by this skill

Base URL: `https://api.yotoplay.com`. Every request except a signed object-store PUT carries `Authorization: Bearer ACCESS_TOKEN`.

## MYO content

- `GET /content/mine` returns `{ "cards": [...] }`. These summary cards may omit chapters.
- `GET /content/{cardId}` returns `{ "card": {...} }` with the complete playlist.
- `POST /content` creates or updates and returns `{ "card": {...} }`. Include `cardId` to update.

The implementation fetches the complete latest card before every mutation, deep-copies it, changes only the requested chapter fields and derived media aggregates, then posts the full document. Unknown fields are preserved. The list endpoint covers user-owned MYO content, not purchased cards.

Each song managed by this skill is one chapter containing exactly one audio track. The stable chapter `key` is the CLI's `song_key`. Existing chapters containing zero or multiple tracks are reported but cannot be changed in v1.

Official references: [content endpoints](https://yoto.dev/api/content/), [get MYO content](https://yoto.dev/api/getUserSMyoContent/), [create/update content](https://yoto.dev/api/createOrUpdateContent/), and [card schema](https://yoto.dev/reference/card-content-schema/).

## Playlist cover

`POST /media/coverImage/user/me/upload?autoconvert=true&coverType=default` centre-crops the input to 638 × 1011 pixels. The skill first contains the entire uncropped source inside a portrait canvas of exactly that size, with safe margins and an optional **PARTIE X/N** badge. `media prepare-cover` saves the upload PNG and a 160 × 254 preview. The API sends only that prepared PNG, never an unprepared square image or an `imageUrl` for Yoto to crop.

The response's `coverImage.mediaUrl` goes in `metadata.cover.imageL` on the full card document. The command fetches the latest card before saving and re-fetches afterward. On a verification mismatch, it waits for the card to settle, rebuilds from the latest document, and retries once. It then downloads the URL from the saved card, checks its dimensions and pixels against the prepared canvas, and returns a preview of the actual published image. It never reports success from the upload response alone. Official references: [upload endpoint and dimensions](https://yoto.dev/api/media/uploadcoverimage/), [cover workflow](https://yoto.dev/myo/uploading-cover-images/).

## MP3 upload and transcode

1. Compute SHA-256 over the local MP3.
2. `GET /media/transcode/audio/uploadUrl?sha256=...&filename=...` returns `{ "upload": { "uploadId", "uploadUrl" } }`.
3. If `uploadUrl` is non-null, PUT the raw MP3 to it with `Content-Type: audio/mpeg`. A null URL means Yoto already has this hash.
4. Poll `GET /media/upload/{uploadId}/transcoded?loudnorm=false`. Its `transcode.transcodedSha256` indicates completion.
5. Use `yoto:#TRANSCODED_SHA256` as `trackUrl` and use `transcodedInfo` for duration, file size, channels, and format where available.

The CLI times out instead of polling forever and stops immediately on an explicit failed/error state. Official guide: [uploading content to MYO cards](https://yoto.dev/myo/uploading-to-cards/).

## Custom icons

`POST /media/displayIcons/user/me/upload?autoConvert=false&filename=...` accepts the raw PNG and returns `displayIcon.mediaId`. Use `yoto:#MEDIA_ID` for both `chapter.display.icon16x16` and `track.display.icon16x16`.

With `autoConvert=false`, Yoto requires an exact 16×16 PNG or GIF; PNG must be 24-bit RGBA rather than indexed. The local converter crops to a square, composites on black, downsamples, limits the palette, converts back to RGBA, and verifies mode and size before upload.

Official references: [upload custom icon endpoint](https://yoto.dev/api/uploadCustomIcon/) and [icon workflow](https://yoto.dev/icons/uploading-icons/).
