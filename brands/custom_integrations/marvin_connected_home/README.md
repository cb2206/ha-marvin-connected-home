# Brand images

Home Assistant does not read logos out of a custom integration's own folder. The
frontend always resolves them from `brands.home-assistant.io`, which is served
from the [home-assistant/brands](https://github.com/home-assistant/brands)
repository. Until the images below are merged there, the integration shows the
generic puzzle-piece placeholder no matter what this repo contains.

## Getting the logo live

1. Fork `home-assistant/brands`.
2. Copy the PNGs in this directory to
   `custom_integrations/marvin_connected_home/` in that fork — the directory
   name must match the `domain` in
   [manifest.json](../../../custom_components/marvin_connected_home/manifest.json).
   Do not copy this README; the brands repo expects images only.
3. Open a PR. Their CI checks dimensions, format and trimming.

Once merged the images are served from
`https://brands.home-assistant.io/marvin_connected_home/icon.png` and appear on
the integrations page, the config-flow dialog and the device page.

## The files

| File | Size | Used for |
| --- | --- | --- |
| `icon.png` | 256×256 | Square mark — integration list, device page |
| `icon@2x.png` | 512×512 | hDPI variant |
| `logo.png` | 291×256 | Full lockup — config flow header |
| `logo@2x.png` | 582×512 | hDPI variant |
| `dark_logo.png` | 291×256 | Lockup with a white wordmark, for dark themes |
| `dark_logo@2x.png` | 582×512 | hDPI variant |

There is deliberately no `dark_icon.png`: the mark is gold on transparent and
reads correctly on both backgrounds, and brands falls back to the undarkened
file when a `dark_` variant is absent. The wordmark is the only part that needed
a dark treatment, because it is pure black in the source artwork.

Regenerate everything from the source SVG with:

```bash
python scripts/brand/build_brand_assets.py
```

## Trademark

The Marvin rose and wordmark are trademarks of Marvin. This integration is not
affiliated with or endorsed by Marvin, and the marks are used only to identify
which product the integration talks to. The brands repo asks that you have the
brand owner's permission before submitting third-party artwork — worth asking
Marvin for explicitly, alongside any API access request, rather than assuming
it.

Source artwork: `scripts/brand/marvin-logo.svg`, retrieved from
`https://www.amaxxwindow.com/web/image/3887-9f36aa28/marvin logo 2.svg` — a
dealer site, not a Marvin-operated one. If Marvin supplies official artwork,
replace the SVG and re-run the build script.
