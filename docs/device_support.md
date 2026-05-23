# Device Support & Validation Status

This page is **auto-generated** from `cpap_parser/device_profiles.py`.
Edit the `PROFILES` dict there to update it.

## Status legend

| Status | Meaning |
|---|---|
| ✅ validated | Parser output verified against a reference (e.g. OSCAR) |
| ⚠️ needs validation | Parser implemented; not formally validated — consider [contributing sample data](https://gitlab.com/open-cpap/cpap-parser/-/issues) |
| ❌ unimplemented | Parser is a stub or absent |

## Supported devices

| Manufacturer | Device | Status | Notes |
|---|---|---|---|
| Löwenstein Medical | Prisma Line (prisma25S, prisma25ST) | ✅ validated | Validated against OSCAR v1.7.x: session match >95%, waveform accuracy baseline established (EPAP ±0.5 hPa). |
| Löwenstein Medical | Eyra / Lumis / SOMNOsoft | ⚠️ needs validation | Parser implemented; no OSCAR validation performed. Please consider contributing sample data. |
| ResMed | AirSense / AirCurve series | ⚠️ needs validation | Parser implemented via cpap_py; no OSCAR validation performed. Please consider contributing sample data. |
| DeVilbiss / IntelliPAP | DV5 / DV6 | ⚠️ needs validation | Parser implemented; no formal validation performed. Please consider contributing sample data. |
| BMC / 3B Medical | G2 / G3 series | ⚠️ needs validation | Parser implemented; no formal validation performed. Please consider contributing sample data. |
| Fisher & Paykel | SleepStyle / ICON series | ⚠️ needs validation | Parser implemented; no formal validation performed. Please consider contributing sample data. |
| Yuwell / DJMed | BreathCare YH series | ⚠️ needs validation | Parser implemented; no formal validation performed. Please consider contributing sample data. |
| Apex Medical | iCH / XT series | ⚠️ needs validation | Parser implemented; no formal validation performed. Please consider contributing sample data. |
| Philips Respironics | DreamStation / System One series | ⚠️ needs validation | Parser implemented; no formal validation performed. Please consider contributing sample data. |
