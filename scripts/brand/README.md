# Brand images

The icon and logo ship inside the integration, at
[`custom_components/marvin_connected_home/brand/`](../../custom_components/marvin_connected_home/brand/).
Home Assistant serves them from there; no external submission is involved.

## How this works

Since **Home Assistant 2026.3**, a custom integration can carry its own brand
images. Core checks for a `brand` entry in the integration directory
(`Integration.has_branding`) and, if present, serves matching files out of
`brand/` through `/api/brands/integration/{domain}/{image}`, ahead of the
`brands.home-assistant.io` CDN.

Before 2026.3 the only route was a PR to
[home-assistant/brands](https://github.com/home-assistant/brands) adding a folder
under `custom_integrations/`. **That route is now closed** — the brands PR
template states that pull requests adding new custom components are no longer
accepted, and its README marks `custom_integrations/` a legacy folder.

On installs older than 2026.3 the `brand/` folder is simply ignored and Home
Assistant falls back to the CDN, where this domain is not registered, so those
users keep seeing the generic placeholder. Nothing breaks; the artwork just
doesn't appear. That is why `hacs.json` still allows 2024.10.0 — the logo is not
worth forcing an upgrade over.

## The files

Core's `ALLOWED_IMAGES` accepts eight names. We ship six; the two omitted ones
resolve through core's documented fallback chain.

| File | Size | Used for |
| --- | --- | --- |
| `icon.png` | 256×256 | Square mark — integration list, device page |
| `icon@2x.png` | 512×512 | hDPI variant |
| `logo.png` | 291×256 | Full lockup — config flow header |
| `logo@2x.png` | 582×512 | hDPI variant |
| `dark_logo.png` | 291×256 | Lockup with a white wordmark, for dark themes |
| `dark_logo@2x.png` | 582×512 | hDPI variant |

No `dark_icon`: the mark is gold on transparent and reads correctly on either
background, and core falls back to `icon.png` when the dark variant is absent.
The wordmark is the only part that needed a dark treatment, because it is pure
black in the source artwork.

Sizes follow the brands repository specification — icons exactly 256/512 square,
logos with a shortest side of 256/512, PNG, transparent, trimmed to minimum empty
space. Core does not re-validate dimensions for local images, but matching the
spec is what makes them render correctly everywhere the frontend uses them.

Regenerate from the source SVG with:

```bash
python scripts/brand/build_brand_assets.py
```

## Trademark

The Marvin rose and wordmark are trademarks of Marvin. This integration is
unofficial and not affiliated with or endorsed by Marvin; the marks identify
which product it talks to, nothing more.

No Home Assistant requirement obliges you to obtain the brand owner's
permission — neither the brands README, its PR template, nor the developer
documentation asks for it. The brands repository states only that trademarks are
the property of their respective owners, are used for identification purposes
only, and that their use does not imply endorsement. This repository takes the
same position. Marvin can of course ask for the artwork to be removed, and that
request would be honoured.

Source artwork: `marvin-logo.svg`, retrieved from
`https://www.amaxxwindow.com/web/image/3887-9f36aa28/marvin logo 2.svg` — a
dealer site, not a Marvin-operated one. If Marvin supplies official artwork,
replace the SVG and re-run the build script.
