// Run: gjs -m tests/test_colors.js
import {hexToHsv, hsvToHex, normalizeHex} from '../extension/g560@an-awful-person.github.io/colors.js';

let failed = 0;
function eq(actual, expected, what) {
    const a = JSON.stringify(actual), e = JSON.stringify(expected);
    if (a !== e) {
        failed++;
        printerr(`FAIL ${what}: got ${a}, want ${e}`);
    }
}

eq(normalizeHex('#3FA0FF'), '#3fa0ff', 'normalize upper');
eq(normalizeHex('f0a'), '#ff00aa', 'normalize short');
eq(normalizeHex('blue'), null, 'normalize invalid');
eq(hsvToHex(0, 100, 100), '#ff0000', 'red');
eq(hsvToHex(120, 100, 100), '#00ff00', 'green');
eq(hsvToHex(240, 100, 100), '#0000ff', 'blue');
eq(hsvToHex(0, 0, 100), '#ffffff', 'white');
eq(hsvToHex(360, 100, 50), '#800000', 'hue wraps');
eq(hexToHsv('#000000'), [0, 0, 0], 'black');
eq(hexToHsv('nope'), null, 'hsv invalid');
for (const hex of ['#3fa0ff', '#ff6a00', '#123456', '#a000ff', '#ffffff', '#00d0ff']) {
    const [h, s, v] = hexToHsv(hex);
    eq(hsvToHex(h, s, v), hex, `round trip ${hex}`);
}

if (failed) {
    printerr(`${failed} color test(s) failed`);
    imports.system.exit(1);
}
print('colors.js: all tests passed');
