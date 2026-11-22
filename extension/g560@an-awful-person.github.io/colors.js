// Pure color helpers (no GI imports, so they can be unit-tested with plain gjs).

export function normalizeHex(value) {
    let v = String(value).trim().toLowerCase().replace(/^#/, '');
    if (/^[0-9a-f]{3}$/.test(v))
        v = v.split('').map(c => c + c).join('');
    return /^[0-9a-f]{6}$/.test(v) ? `#${v}` : null;
}

// h: 0-360, s/v: 0-100  ->  '#rrggbb'
export function hsvToHex(h, s, v) {
    h = ((h % 360) + 360) % 360;
    s = Math.max(0, Math.min(100, s)) / 100;
    v = Math.max(0, Math.min(100, v)) / 100;
    const c = v * s;
    const x = c * (1 - Math.abs((h / 60) % 2 - 1));
    const m = v - c;
    const [r, g, b] = h < 60 ? [c, x, 0] : h < 120 ? [x, c, 0] : h < 180 ? [0, c, x]
        : h < 240 ? [0, x, c] : h < 300 ? [x, 0, c] : [c, 0, x];
    const hex = n => Math.round((n + m) * 255).toString(16).padStart(2, '0');
    return `#${hex(r)}${hex(g)}${hex(b)}`;
}

// '#rrggbb' -> [h 0-360, s 0-100, v 0-100]  (null for invalid input)
export function hexToHsv(value) {
    const hex = normalizeHex(value);
    if (!hex)
        return null;
    const [r, g, b] = [1, 3, 5].map(i => parseInt(hex.slice(i, i + 2), 16) / 255);
    const max = Math.max(r, g, b), min = Math.min(r, g, b), d = max - min;
    let h = 0;
    if (d) {
        if (max === r)
            h = 60 * (((g - b) / d) % 6);
        else if (max === g)
            h = 60 * ((b - r) / d + 2);
        else
            h = 60 * ((r - g) / d + 4);
    }
    return [(h + 360) % 360, max ? (d / max) * 100 : 0, max * 100];
}
