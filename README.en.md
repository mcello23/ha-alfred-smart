# Alfred Smart for Home Assistant

[🇪🇸 Español](README.md) · 🇬🇧 English

**Unofficial** [Alfred Smart](https://alfredsmart.com) integration for Home
Assistant: open the gates, garage doors and common areas of your building,
with automatic discovery of every access your account can use.

> [!IMPORTANT]
> Not affiliated with or endorsed by Alfred Smart Systems S.L. It uses the same
> undocumented API as the web app ([app.alfredsmart.com](https://app.alfredsmart.com)),
> which may change without notice. These entities open real doors.

## Features

- Sign in with your **email and password**; the session renews itself.
- **Automatic discovery** of gates, garages and doors, including the ones shared
  with the community, grouped under their gateway.
- An **Open button** per access. It fails loudly when Alfred could not open,
  instead of pretending it worked.
- A **Last opening** sensor per access (`ok`, `gateway_unreachable`,
  `unauthorized`, `forbidden`, `timeout`, `error`).
- An **Alfred connection** diagnostic sensor that tolerates single missed polls
  (15-minute grace period).
- **Common areas**: `alfred_smart.book_common_area` and
  `alfred_smart.open_common_area` actions.
- UI in English, Spanish and Brazilian Portuguese.

## Installation

HACS → ⋮ → **Custom repositories** → add
`https://github.com/__GITHUB_USER__/ha-alfred-smart` as **Integration**, download
**Alfred Smart** and restart. Then **Settings → Devices & services → Add
integration → Alfred Smart**.

If your home cannot be discovered, you will be asked for its code (`asset_id`):
open app.alfredsmart.com, press F12 → **Network**, open your home and look for a
request to `devices`; the `asset_id=` parameter is the code.

## When something does not open

| You see | Meaning | What to do |
| --- | --- | --- |
| Gateway offline (HTTP 502/503/504) | Alfred's server cannot reach the gate's physical gateway. | Nothing to fix in Home Assistant: use your key and tell building management. |
| Session rejected (401) | Password changed. | Re-authenticate when Home Assistant asks. |
| Not allowed (403) | Your account sees the access but cannot use it. | Ask building management. |
| No answer | Alfred did not reply within 30 s. | **It may have opened anyway.** The integration never retries an opening. |

"Enabled" does not mean "working": a gate whose gateway is down is still listed
as enabled, so only an opening attempt finds out. That is what the *Last
opening* sensor is for.

## Discovery script

`scripts/alfred_discover.py` (Python 3 only, read-only) lists everything the
integration would see. `--dump file.json` writes the raw answers, redacted; attach
it (or the integration's **diagnostics** download) to an issue if discovery
misses something in your building.

## License

[MIT](LICENSE)
