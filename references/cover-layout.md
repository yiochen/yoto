# Yoto cover layout checks

Yoto's default cover upload centre-crops to 638 × 1011. Padding an image that has already been cropped cannot restore missing titles or artwork. Inspect the current published cover first; when content is missing, find the uncropped art embedded in the audiobook or from the exact publisher or audiobook edition.

Prepare each cover with `media prepare-cover`. This fits the complete source into a portrait canvas with safe margins. Optional heading and title text can use the spare space when the source's own lettering is too small at card size. Do not redraw or invent text printed on the publisher's art. For a book split across playlists, create one prepared file per playlist with a distinct high-contrast `1/N`, `2/N`, etc. corner badge. The fraction counts parts of that book, not tomes in a series.

Inspect the returned 160 × 254 `preview` before using `cards set-cover`. Check the source's left and right edges, title and author, any added caption, and the badge. Keep text clear of the edges and the app's central play button. After the command succeeds, inspect its `published.preview` and full `published.image`; the upload response alone does not prove that the right image is on the card.

When another process is editing the playlist, `cards set-cover` re-reads the full card and retries one failed verification after the card settles. If it still returns `update_not_verified`, re-read the playlist and report the unresolved state. Report a cover as saved only after its URL and published image are verified.

For a whole library, list the MYO playlists before and after the work to catch new playlists created during the edit. Preserve already correct covers and never change purchased card artwork.
