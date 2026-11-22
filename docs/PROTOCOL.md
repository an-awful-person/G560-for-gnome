# Logitech G560 HID++ protocol notes

These notes describe how this project talks to the speaker. Every layout below was confirmed by writing a value and reading it back on a real G560 with firmware 122.3.23, unless it says otherwise.

## Transport

| | |
|---|---|
| USB ID | `046d:0a78` |
| Interface | hidraw node whose report descriptor declares usage page `0xFF43` (vendor) |
| Protocol | HID++ 2.0 (the device reports version 4.2) |
| Report | long report `0x11`, 20 bytes: `11 FF <feature index> <function << 4 \| sw id> <params…>` |
| Device index | `0xFF` (directly attached) |
| Errors | `11 FF FF <feature index> <fn/sw id> <error code>` |

Feature **indexes** differ between firmware versions. Always look them up with IRoot `getFeature` (feature index 0, fn 0, parameter = feature id). HID input reports are delivered to every process that has the hidraw node open, so match replies on feature index, function and software id.

Feature list reported by the device:

| Index | Feature | Meaning |
|---|---|---|
| 0 | `0x0000` | IRoot |
| 1 | `0x0001` | IFeatureSet |
| 2 | `0x0003` | Firmware info (`U1` 122.3.23) |
| 3 | `0x0005` | Device name/type |
| 4 | `0x8070` | Color LED effects |
| 5 | `0x8010` | G-keys (unused here) |
| 6 | `0x8310` | Equalizer |
| 7 | `0x8320` | Unknown (unused here) |
| 8 | `0x8040` | Brightness |
| 9 | `0x8305` | Subwoofer level |

## Subwoofer level: `0x8305`

| Function | Request | Reply |
|---|---|---|
| fn0 get | – | `[level]` (0–100) |
| fn1 set | `[level]` | echo |

The speaker reportedly forgets this value when it loses USB, so `g560d` sets it again on every reconnect.

## Brightness: `0x8040`

| Function | Request | Reply |
|---|---|---|
| fn0 info | – | `[0, max=100, steps=4, …]` |
| fn1 get | – | `[hi, lo]`, big-endian 0–100 |
| fn2 set | `[hi, lo]` | echo |

## LED effects: `0x8070`

There are four zones: `0` rear left, `1` rear right, `2` front left, `3` front right.

| Function | Request | Reply |
|---|---|---|
| fn0 info | – | `[zone count = 4, …]` |
| fn1 zone info | `[zone]` | `[zone, location(2), effect count, …]` |
| fn2 effect info | `[zone, slot]` | `[zone, slot, effect id(2), caps…]` |
| fn3 set effect | `[zone, slot, p0..p9, persist, 0, 0, 0]` | – |
| fn4 get setting | `[0, 1]` | `[0, 1, value]`, startup animation: 1 = on, 2 = off |
| fn5 set setting | `[0, 1, value]` | echo (persistent) |
| fn14 get effect | `[zone, source]`, source 0 = live, 1 = saved | `[zone, slot, p0..p9]` |

`persist` = 1 stores the effect in the speaker's flash, so it survives a power cycle without software. This project writes with `persist = 0` and re-applies its settings itself. Only **Save lights to speaker** uses `persist = 1`.

| Slot | Effect id | Effect | Parameters `p0..` |
|---|---|---|---|
| 0 | `0x0000` | Off | – |
| 1 | `0x0001` | Solid | `r g b` |
| 2 | `0x0003` | Color cycle | `00 00 00 00 00 period_hi period_lo brightness` |
| 3 | `0x0007` | Audio visualizer (runs on the speaker) | factory value: `01 00 00 00 00 13 88` (meaning unknown) |
| 4 | `0x000A` | Breathing | `r g b period_hi period_lo 00 brightness` |

The period is in milliseconds (100–60000). Brightness is 1–100.

Quirk: on the test unit, the *factory* cycle effect reads back with its period one byte later than the layout above. Values this project writes read back exactly as written. The parser tolerates both forms.

## Equalizer: `0x8310`

| Function | Request | Reply |
|---|---|---|
| fn0 info | – | `[bands = 10, range = 26, 0, min = -20, max = +6]` (signed dB) |
| fn1 frequencies | `[start index]` | `[start, f(2) × 7]`, big-endian Hz |
| fn2 get | `[0]` | `[g0 … g9]`, signed dB (no echo byte) |
| fn3 set | `[0x02, g0 … g9]` | `[0x02, g0 … g9]` |

The bands are 32, 64, 125, 250 and 500 Hz, then 1, 2, 4, 8 and 16 kHz. The EQ runs on the speaker's own DSP, so it works with any audio source.

## Safety

This project only sends HID++ **feature** calls listed above. The speaker also exposes a Conexant memory-access/EEPROM interface. Writing to it can permanently brick the speaker, and there is no official recovery, so this code never uses it.

## Sources

- [mugurc/g560-reverse-engineering](https://github.com/mugurc/g560-reverse-engineering) (MIT): LED, brightness and startup-animation layouts, plus the risk documentation.
- [hello-nexus/nexus-service#15](https://github.com/hello-nexus/nexus-service/pull/15): subwoofer feature `0x8305`.
- The EQ layout and LED read-back details were verified for this project with read-back tests.
