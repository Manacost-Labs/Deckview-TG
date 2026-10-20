# Deckview HTTP API

Публичный HTTP API для рендера Hearthstone-колод, распознавания архетипов и перевода названий. Документ сохраняет англоязычные примеры запросов, чтобы ими было удобно пользоваться из кода.

Base URL:

```text
https://api.blizzcore.ru
```

Legacy server URL:

```text
http://135.125.171.168/deckview-api/v1
```

The public API is designed to work without an API key. You can call the endpoints directly from a backend, browser, WordPress plugin, static site, or Telegram bot.

## Authentication

No authentication is required for public endpoints:

- `GET /`
- `GET /health`
- `GET|POST /render`
- `GET|POST /translate`
- `GET|POST /archetype`
- `GET /archetypes`
- `GET|POST /v1/render`
- `GET|POST /v1/translate`
- `GET|POST /v1/archetype`
- `GET /v1/archetypes`

`/publish` is not public because it can publish posts to WordPress and Telegram. It remains protected on the server and is intentionally not part of the public API contract.

## CORS

The API sends permissive CORS headers for public endpoints:

```http
Access-Control-Allow-Origin: *
Access-Control-Allow-Methods: GET, POST, OPTIONS
Access-Control-Allow-Headers: Authorization, Content-Type, X-API-Key
```

## Rate Limit

Public API traffic is rate-limited at the nginx layer:

```text
30 requests per minute per IP, burst 20
```

If you plan to send large batches, add client-side retries with backoff.

## Endpoint Summary

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/` | API index with available endpoints |
| `GET` | `/health` | Service status |
| `GET`, `POST` | `/render` | Render a deck image and return metadata + image URL |
| `GET`, `POST` | `/translate` | Translate deck/archetype names through the local EN -> RU table |
| `GET`, `POST` | `/archetype` | Recognize a deck archetype from a deck code using HSGuru |
| `GET` | `/archetypes` | List known archetype translations |
| `GET`, `POST` | `/v1/*` | Versioned aliases for the same public endpoints |

## API Index

```bash
curl https://api.blizzcore.ru/
```

Example response:

```json
{
  "success": true,
  "service": "deckview-api",
  "version": "v1",
  "endpoints": {
    "health": "/health",
    "render": "/render",
    "translate": "/translate",
    "archetype": "/archetype",
    "archetypes": "/archetypes",
    "v1": "/v1"
  }
}
```

## Health

```bash
curl https://api.blizzcore.ru/health
```

Example response:

```json
{
  "success": true,
  "service": "deckview",
  "status": "ok",
  "public_api": true,
  "auth_required": false,
  "publish_auth_required": true,
  "endpoints": {
    "render": "/deckview-api/v1/render",
    "translate": "/deckview-api/v1/translate",
    "archetype": "/deckview-api/v1/archetype",
    "archetypes": "/deckview-api/v1/archetypes",
    "publish": "/deckview-api/v1/publish"
  }
}
```

## Render Deck

Generates a deck image from a Hearthstone deck code.

```http
POST /render
Content-Type: application/json
```

Request body:

```json
{
  "deck_code": "AAEC...",
  "deck_name": "Optional deck title"
}
```

`deck_name` is optional. If omitted, the image is generated without a custom title.

`image_resolution` accepts `standard` (default, up to 1920 px on the longest
side) or `high` (up to 3840 px). High resolution preserves the original card
composition before the final reduction; it never enlarges a smaller canvas.
It uses JPEG quality 95 and 4:4:4 color sampling. Each resolution has a separate
render cache and async job identity. Unsupported values return HTTP 400 with
`INVALID_IMAGE_RESOLUTION`.

For article images without a heading:

```json
{
  "deck_code": "AAEC...",
  "image_style": "parchment",
  "image_resolution": "high",
  "async": true
}
```

The same parameter works with GET and all public render aliases. Both immediate
responses and completed async job responses report `image_resolution`.
Use `image_url` for the original; `preview_image_url` remains a small preview.

In Telegram, send `/hq DECK_CODE` (optionally followed by a title). The bot uses
your current image design and delivers the high resolution JPG as a document,
without Telegram photo compression. This command requires the render queue;
if the queue is unavailable, it asks you to retry. Regular deck messages keep
their current photo delivery and resolution.

### Render With cURL

```bash
curl -X POST "https://api.blizzcore.ru/render" \
  -H "Content-Type: application/json" \
  --data '{
    "deck_code": "AAECAR8G054GmacHmqcHm6cHxbEHy7YHDKmfBKqfBK+SB4WVB86bB+6fB5CnB5inB9SvB7TAB7nAB7vABwAA",
    "deck_name": "No Hand Hunter"
  }'
```

### Render With GET

```bash
curl "https://api.blizzcore.ru/render?deck_code=AAEC...&deck_name=No%20Hand%20Hunter"
```

Accepted query aliases:

- `deck_code`
- `deck`
- `deck_name`
- `name`

### Render Response

```json
{
  "success": true,
  "cached": false,
  "deck_code": "AAEC...",
  "deck_name": "No Hand Hunter",
  "cost": 3640,
  "deck_class": "Охотник",
  "deck_mode": "Стандарт",
  "filename": "deck_20260526_214630_574807.jpg",
  "image_path": "/static/generated/deck_20260526_214630_574807.jpg",
  "image_url": "https://api.blizzcore.ru/static/generated/deck_20260526_214630_574807.jpg",
  "preview_filename": "render-cache/ab/hash.preview-v1.webp",
  "preview_image_path": "/static/generated/render-cache/ab/hash.preview-v1.webp",
  "preview_image_url": "https://api.blizzcore.ru/static/generated/render-cache/ab/hash.preview-v1.webp"
}
```

Use `preview_image_url` for catalogue cards and `image_url` for downloads or a
full-size viewer. Preview fields are optional so clients remain compatible with
older cached results; fall back to `image_url` when they are absent.

### Render From JavaScript

```js
const response = await fetch("https://api.blizzcore.ru/render", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({
    deck_code: "AAECAR8G054GmacHmqcHm6cHxbEHy7YHDKmfBKqfBK+SB4WVB86bB+6fB5CnB5inB9SvB7TAB7nAB7vABwAA",
    deck_name: "No Hand Hunter"
  })
});

const deck = await response.json();
console.log(deck.image_url);
```

### Render From Python

```python
import requests

payload = {
    "deck_code": "AAECAR8G054GmacHmqcHm6cHxbEHy7YHDKmfBKqfBK+SB4WVB86bB+6fB5CnB5inB9SvB7TAB7nAB7vABwAA",
    "deck_name": "No Hand Hunter",
}

response = requests.post("https://api.blizzcore.ru/render", json=payload, timeout=180)
response.raise_for_status()
print(response.json()["image_url"])
```

## Translate Deck Name

Translates one or more deck/archetype names through the local archetype table.

```http
POST /translate
Content-Type: application/json
```

Single-name request:

```json
{
  "name": "No Hand Hunter"
}
```

Multi-name request:

```json
{
  "names": ["No Hand Hunter", "Big Druid"]
}
```

GET is also supported:

```bash
curl "https://api.blizzcore.ru/translate?name=No%20Hand%20Hunter"
```

Example response:

```json
{
  "success": true,
  "count": 1,
  "translated": "Фейс Охотник",
  "items": [
    {
      "source": "No Hand Hunter",
      "translated": "Фейс Охотник"
    }
  ]
}
```

## Recognize Archetype

Recognizes a Hearthstone deck archetype from a deck code. HSGuru is used as the primary source.

```http
POST /archetype
Content-Type: application/json
```

Request body:

```json
{
  "deck_code": "AAEC..."
}
```

GET is also supported:

```bash
curl "https://api.blizzcore.ru/archetype?deck_code=AAEC..."
```

Accepted query aliases:

- `deck_code`
- `deck`

Example response:

```json
{
  "success": true,
  "source": "hsguru_cloudscraper",
  "cached": false,
  "deck_code": "AAEC...",
  "archetype": "Фейс Охотник",
  "archetype_raw": "No Hand Hunter",
  "deck_name_raw": "No Hand Hunter",
  "class": "",
  "format": "",
  "translation": "exact",
  "error": null
}
```

Response fields:

| Field | Type | Description |
|---|---|---|
| `success` | boolean | Whether recognition succeeded |
| `source` | string | Data source, for example `hsguru_cloudscraper` or `cache` |
| `cached` | boolean | Whether the response came from local cache |
| `deck_code` | string | Original deck code |
| `archetype` | string | Translated archetype if available, otherwise raw HSGuru name |
| `archetype_raw` | string | Raw archetype returned by HSGuru |
| `deck_name_raw` | string | Raw deck name returned by HSGuru |
| `class` | string | Class if returned by source |
| `format` | string | Format if returned by source |
| `translation` | string | `exact`, `partial`, or `none` |
| `error` | string or null | Error message when `success` is `false` |

## List Archetype Translations

Returns the local EN -> RU archetype translation table.

```bash
curl "https://api.blizzcore.ru/archetypes?search=hunter&limit=50"
```

Query parameters:

| Parameter | Type | Default | Description |
|---|---:|---:|---|
| `search` | string | empty | Optional substring filter |
| `limit` | integer | `200` | Number of rows, from `1` to `500` |

Example response:

```json
{
  "success": true,
  "count": 1,
  "items": [
    {
      "id": 16,
      "name_en": "no hand hunter",
      "name_ru": "Фейс Охотник"
    }
  ]
}
```

## Versioned Endpoints

Every public endpoint is also available under `/v1`.

Examples:

```bash
curl https://api.blizzcore.ru/v1/health
curl "https://api.blizzcore.ru/v1/archetype?deck_code=AAEC..."
curl -X POST https://api.blizzcore.ru/v1/render \
  -H "Content-Type: application/json" \
  --data '{"deck_code":"AAEC..."}'
```

The legacy path is also available for compatibility:

```text
https://api.blizzcore.ru/deckview-api/v1
```

## Static Images

`/render` returns an `image_url` like:

```text
https://api.blizzcore.ru/static/generated/deck_20260526_214630_574807.jpg
```

Generated images are served from `/static/generated/...`.

## Errors

Common HTTP statuses:

| Status | Meaning |
|---:|---|
| `200` | Success |
| `400` | Required parameter is missing |
| `401` | Private endpoint requires authorization |
| `405` | Method is not allowed |
| `422` | Deck code could not be rendered |
| `429` | Rate limit exceeded |
| `500` | Internal server error |
| `504` | Render timed out |

Error response example:

```json
{
  "success": false,
  "error": "deck_code required"
}
```

## WordPress Example

Minimal shortcode-style browser usage:

```html
<form id="deck-form">
  <textarea name="deck_code" placeholder="Paste Hearthstone deck code"></textarea>
  <button type="submit">Render</button>
</form>
<img id="deck-image" alt="" />

<script>
document.querySelector("#deck-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const deckCode = event.target.deck_code.value.trim();

  const response = await fetch("https://api.blizzcore.ru/render", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ deck_code: deckCode })
  });

  const result = await response.json();
  if (!result.success) {
    alert(result.error || "Render failed");
    return;
  }

  document.querySelector("#deck-image").src = result.image_url;
});
</script>
```

## Public Contract

The stable public surface is:

```text
https://api.blizzcore.ru/health
https://api.blizzcore.ru/render
https://api.blizzcore.ru/translate
https://api.blizzcore.ru/archetype
https://api.blizzcore.ru/archetypes
```

Do not rely on private publishing endpoints for public integrations.
