# Deckview Changelog

## Unreleased

- Upload a lighter q85 4:2:0 copy of each new deck render to Telegram
  (~39% smaller on the Reno fixtures); the q92 artifact stays for the render
  cache and downloads. New `telegram_photo_ms` perf field.
- Stopped decoding the Rust JPEG back into pixels on every render; the PIL
  image is now opened lazily (~20 ms saved per render).
- Deck render errors no longer show exception text or `BATTLE_NET_TOKEN` to
  users; the bot and worker share three plain messages from
  `deckview.services.deck_errors`.
- RQ timeouts and stops now replace the "Собираю колоду" status instead of
  leaving it forever.
- Private chats get a hint when a message has no deck code; quoted codes are
  recognised and short `AA…` words in groups no longer trigger renders.
- `/health` is listed only in admin chats and admin `/help`; `/start` in
  groups greets the sender instead of "None".

- Fixed Telegram download buttons expiring with the two-hour temporary render
  directory: new buttons resolve immutable cached artifacts, while existing
  buttons can recover the photo already stored by Telegram.
- Added versioned, immutable 720px WebP preview derivatives for API deck
  renders while preserving the original JPEG for downloads and lightboxes.
- Backfilled previews on warm API cache hits without adding decode work to the
  Telegram render path.
- Added atomic concurrent publication, corrupt-preview recovery, path
  validation, cache telemetry fields, long-lived nginx caching, and regression
  coverage for preview generation and API responses.
