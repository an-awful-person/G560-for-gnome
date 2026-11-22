// G560 settings window: per-zone lights, 10-band EQ editor + presets, general options.
import Adw from 'gi://Adw';
import Gdk from 'gi://Gdk';
import GLib from 'gi://GLib';
import Gtk from 'gi://Gtk';

import {ExtensionPreferences} from 'resource:///org/gnome/Shell/Extensions/js/extensions/prefs.js';

import {DaemonClient, EFFECTS} from './dbus.js';

const ZONES = [
    ['front-left', 'Front left'],
    ['front-right', 'Front right'],
    ['rear-left', 'Rear left'],
    ['rear-right', 'Rear right'],
];

function debounce(ms, fn) {
    let id = 0;
    return (...args) => {
        if (id)
            GLib.source_remove(id);
        id = GLib.timeout_add(GLib.PRIORITY_DEFAULT, ms, () => {
            id = 0;
            fn(...args);
            return GLib.SOURCE_REMOVE;
        });
    };
}

function toRgba(hex) {
    const c = new Gdk.RGBA();
    c.parse(hex);
    return c;
}

function toHex(rgba) {
    const h = v => Math.round(v * 255).toString(16).padStart(2, '0');
    return `#${h(rgba.red)}${h(rgba.green)}${h(rgba.blue)}`;
}

export default class G560Prefs extends ExtensionPreferences {
    fillPreferencesWindow(window) {
        window.set_default_size(720, 760);
        this._updating = false;
        this._state = null;
        this._client = new DaemonClient(st => this._setState(st));
        window.connect('close-request', () => {
            this._client.destroy();
            return false;
        });

        this._banner = new Adw.Banner({title: 'g560d is not running or the speaker is unplugged'});
        window.add(this._lightsPage());
        window.add(this._eqPage());
        window.add(this._generalPage());
        this._client.refresh();
    }

    // --- Lights -------------------------------------------------------------
    _lightsPage() {
        const page = new Adw.PreferencesPage({title: 'Lights', icon_name: 'color-select-symbolic'});
        if ('banner' in page) {
            page.banner = this._banner;
        } else {
            const bg = new Adw.PreferencesGroup();
            bg.add(this._banner);
            page.add(bg);
        }

        const top = new Adw.PreferencesGroup({title: 'All zones'});
        this._brightRow = new Adw.SpinRow({
            title: 'Brightness', subtitle: 'Overall LED brightness',
            adjustment: new Gtk.Adjustment({lower: 0, upper: 100, step_increment: 5}),
        });
        const setBright = debounce(150, v => this._client.setBrightness(v));
        this._brightRow.connect('notify::value', r => !this._updating && setBright(r.value));
        top.add(this._brightRow);

        this._linkRow = new Adw.SwitchRow({title: 'Link front and rear in the top-bar menu'});
        this._linkRow.connect('notify::active', r => !this._updating &&
            this._client.setOption('link_zones', GLib.Variant.new_boolean(r.active)));
        top.add(this._linkRow);

        const save = new Adw.ButtonRow({title: 'Save current lights to speaker'});
        save.connect('activated', () => this._client.saveToSpeaker());
        top.add(save);
        page.add(top);

        this._zoneRows = {};
        const effectNames = Gtk.StringList.new(EFFECTS.map(e => e[1]));
        for (const [zone, label] of ZONES) {
            const g = new Adw.PreferencesGroup({title: label});
            const effect = new Adw.ComboRow({title: 'Effect', model: effectNames});
            const colorBtn = new Gtk.ColorDialogButton({
                dialog: new Gtk.ColorDialog({with_alpha: false}), valign: Gtk.Align.CENTER,
            });
            const color = new Adw.ActionRow({title: 'Color'});
            color.add_suffix(colorBtn);
            const period = new Adw.SpinRow({
                title: 'Speed', subtitle: 'Period in milliseconds',
                adjustment: new Gtk.Adjustment({lower: 100, upper: 60000, step_increment: 250, page_increment: 1000}),
            });
            const level = new Adw.SpinRow({
                title: 'Effect brightness',
                adjustment: new Gtk.Adjustment({lower: 1, upper: 100, step_increment: 5}),
            });
            for (const r of [effect, color, period, level])
                g.add(r);
            page.add(g);

            const push = debounce(150, () => {
                const eff = EFFECTS[effect.selected][0];
                this._client.setZone(zone, {
                    effect: eff, color: toHex(colorBtn.rgba),
                    period: Math.round(period.value), brightness: Math.round(level.value),
                });
            });
            effect.connect('notify::selected', () => {
                this._syncZoneSensitivity(this._zoneRows[zone]);
                if (!this._updating)
                    push();
            });
            colorBtn.connect('notify::rgba', () => {
                if (this._updating)
                    return;
                push();
                const hex = toHex(colorBtn.rgba);
                if (!(this._state?.swatches ?? []).includes(hex))
                    this._client.addRecentColor(hex);
            });
            period.connect('notify::value', () => !this._updating && push());
            level.connect('notify::value', () => !this._updating && push());
            this._zoneRows[zone] = {effect, color, colorBtn, period, level};
        }
        return page;
    }

    _syncZoneSensitivity(rows) {
        const eff = EFFECTS[rows.effect.selected][0];
        rows.color.sensitive = eff === 'solid' || eff === 'breathe';
        rows.period.sensitive = eff === 'cycle' || eff === 'breathe';
        rows.level.sensitive = eff === 'cycle' || eff === 'breathe';
    }

    // --- Equalizer ----------------------------------------------------------
    _eqPage() {
        const page = new Adw.PreferencesPage({title: 'Equalizer', icon_name: 'multimedia-equalizer-symbolic'});
        const g = new Adw.PreferencesGroup({
            title: 'Hardware equalizer',
            description: 'Runs on the speaker itself (10 bands, −20 to +6 dB).',
        });
        this._presetModel = new Gtk.StringList();
        this._presetRow = new Adw.ComboRow({title: 'Sound profile', model: this._presetModel});
        this._presetRow.connect('notify::selected', r => {
            if (this._updating || r.selected === Gtk.INVALID_LIST_POSITION)
                return;
            const name = this._presetModel.get_string(r.selected);
            if (name !== 'Custom')
                this._client.setEqPreset(name);
        });
        g.add(this._presetRow);

        const bands = new Gtk.Box({spacing: 6, homogeneous: true, margin_top: 12, margin_bottom: 12,
            height_request: 260});
        this._bands = [];
        const freqs = [32, 64, 125, 250, 500, 1000, 2000, 4000, 8000, 16000];
        const pushEq = debounce(200, () => this._client.setEq(this._bands.map(s => s.get_value())));
        for (const f of freqs) {
            const col = new Gtk.Box({orientation: Gtk.Orientation.VERTICAL, spacing: 4});
            const scale = new Gtk.Scale({
                orientation: Gtk.Orientation.VERTICAL, inverted: true, vexpand: true,
                adjustment: new Gtk.Adjustment({lower: -20, upper: 6, step_increment: 1, page_increment: 3}),
                draw_value: true, value_pos: Gtk.PositionType.TOP, digits: 0,
            });
            scale.add_mark(0, Gtk.PositionType.RIGHT, null);
            scale.connect('value-changed', s => {
                s.set_value(Math.round(s.get_value()));
                if (!this._updating)
                    pushEq();
            });
            col.append(scale);
            col.append(new Gtk.Label({label: f >= 1000 ? `${f / 1000}k` : `${f}`, css_classes: ['caption']}));
            bands.append(col);
            this._bands.push(scale);
        }
        const bandsGroup = new Adw.PreferencesGroup();
        bandsGroup.add(bands);

        const manage = new Adw.PreferencesGroup({title: 'Custom profiles'});
        const nameRow = new Adw.EntryRow({title: 'Save current curve as…', show_apply_button: true});
        nameRow.connect('apply', r => {
            const name = r.text.trim();
            if (name)
                this._client.savePreset(name, this._bands.map(s => s.get_value()));
            r.text = '';
        });
        manage.add(nameRow);
        this._deleteRow = new Adw.ButtonRow({title: 'Delete selected custom profile', css_classes: ['destructive-action']});
        this._deleteRow.connect('activated', () => {
            const st = this._state;
            if (st?.eq_preset && !st.builtin_presets.includes(st.eq_preset))
                this._client.deletePreset(st.eq_preset);
        });
        manage.add(this._deleteRow);

        page.add(g);
        page.add(bandsGroup);
        page.add(manage);
        return page;
    }

    // --- General ------------------------------------------------------------
    _generalPage() {
        const page = new Adw.PreferencesPage({title: 'General', icon_name: 'preferences-system-symbolic'});
        const g = new Adw.PreferencesGroup({title: 'Speaker'});
        this._subRow = new Adw.SpinRow({
            title: 'Subwoofer level', subtitle: 'Re-applied automatically when the speaker reconnects',
            adjustment: new Gtk.Adjustment({lower: 0, upper: 100, step_increment: 5}),
        });
        const setSub = debounce(150, v => this._client.setSubwoofer(v));
        this._subRow.connect('notify::value', r => !this._updating && setSub(r.value));
        g.add(this._subRow);

        this._startupRow = new Adw.SwitchRow({
            title: 'Startup animation', subtitle: 'Rainbow animation at power-on (stored on the speaker)',
        });
        this._startupRow.connect('notify::active', r => !this._updating && this._client.setStartupAnimation(r.active));
        g.add(this._startupRow);
        page.add(g);

        const b = new Adw.PreferencesGroup({title: 'Behaviour'});
        this._lockRow = new Adw.SwitchRow({title: 'Lights off when locked or suspended'});
        this._lockRow.connect('notify::active', r => !this._updating &&
            this._client.setOption('lights_off_on_lock', GLib.Variant.new_boolean(r.active)));
        b.add(this._lockRow);
        this._stepRow = new Adw.SpinRow({
            title: 'Scroll step', subtitle: 'Subwoofer change per scroll on the top-bar icon',
            adjustment: new Gtk.Adjustment({lower: 1, upper: 25, step_increment: 1}),
        });
        this._stepRow.connect('notify::value', r => !this._updating &&
            this._client.setOption('scroll_step', GLib.Variant.new_int32(r.value)));
        b.add(this._stepRow);
        page.add(b);
        return page;
    }

    // --- state --------------------------------------------------------------
    _setState(st) {
        this._state = st;
        const ok = !!st?.connected;
        this._banner.revealed = !ok;
        if (!st)
            return;
        this._updating = true;
        try {
            this._brightRow.value = st.brightness;
            this._linkRow.active = st.link_zones;
            this._subRow.value = st.subwoofer;
            this._lockRow.active = st.lights_off_on_lock;
            this._stepRow.value = st.scroll_step;
            this._startupRow.sensitive = ok && st.startup_animation !== null;
            if (st.startup_animation !== null)
                this._startupRow.active = st.startup_animation;

            for (const [zone] of ZONES) {
                const rows = this._zoneRows[zone];
                const z = st.zones[zone];
                rows.effect.selected = Math.max(0, EFFECTS.findIndex(e => e[0] === z.effect));
                rows.colorBtn.rgba = toRgba(z.color);
                rows.period.value = z.period;
                rows.level.value = z.brightness;
                this._syncZoneSensitivity(rows);
            }

            const names = Object.keys(st.presets);
            const list = st.eq_preset ? names : [...names, 'Custom'];
            const current = [];
            for (let i = 0; i < this._presetModel.get_n_items(); i++)
                current.push(this._presetModel.get_string(i));
            if (JSON.stringify(current) !== JSON.stringify(list))
                this._presetModel.splice(0, current.length, list);
            this._presetRow.selected = list.indexOf(st.eq_preset || 'Custom');
            st.eq.forEach((v, i) => this._bands[i]?.set_value(v));
            this._deleteRow.sensitive = !!st.eq_preset && !st.builtin_presets.includes(st.eq_preset);
        } finally {
            this._updating = false;
        }
    }
}
