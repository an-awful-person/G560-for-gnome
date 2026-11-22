// G560 top-bar indicator: subwoofer, LEDs, EQ presets. All state lives in g560d.
import Clutter from 'gi://Clutter';
import GLib from 'gi://GLib';
import Gio from 'gi://Gio';
import GObject from 'gi://GObject';
import St from 'gi://St';

import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as PanelMenu from 'resource:///org/gnome/shell/ui/panelMenu.js';
import * as PopupMenu from 'resource:///org/gnome/shell/ui/popupMenu.js';
import {Slider} from 'resource:///org/gnome/shell/ui/slider.js';

import {hexToHsv, hsvToHex, normalizeHex} from './colors.js';
import {DaemonClient, EFFECTS, SWATCHES} from './dbus.js';

const ICON = 'audio-speakers-symbolic';

const SliderItem = GObject.registerClass(
class SliderItem extends PopupMenu.PopupBaseMenuItem {
    // opts: {max: 100, onLive: v => {}}; onChange is debounced, onLive fires on every move.
    _init(iconName, label, onChange, opts = {}) {
        super._init({activate: false});
        this._onChange = onChange;
        this._onLive = opts.onLive ?? null;
        this._max = opts.max ?? 100;
        this._updating = false;
        this._timeout = 0;
        if (iconName)
            this.add_child(new St.Icon({icon_name: iconName, style_class: 'popup-menu-icon'}));
        const box = new St.BoxLayout({orientation: Clutter.Orientation.VERTICAL, x_expand: true});
        this._label = new St.Label({text: label, style_class: 'g560-slider-label'});
        box.add_child(this._label);
        this.slider = new Slider(0);
        this.slider.x_expand = true;
        box.add_child(this.slider);
        this.add_child(box);
        this._value = new St.Label({text: '', y_align: Clutter.ActorAlign.CENTER,
            style_class: 'g560-slider-value'});
        this.add_child(this._value);
        this.slider.connect('notify::value', () => {
            const v = this.value;
            this._value.text = `${v}`;
            if (this._updating)
                return;
            this._onLive?.(v);
            if (this._timeout)
                GLib.source_remove(this._timeout);
            this._timeout = GLib.timeout_add(GLib.PRIORITY_DEFAULT, 120, () => {
                this._timeout = 0;
                this._onChange?.(v);
                return GLib.SOURCE_REMOVE;
            });
        });
        this.connect('destroy', () => {
            if (this._timeout)
                GLib.source_remove(this._timeout);
            this._timeout = 0;
        });
    }

    get value() {
        return Math.round(this.slider.value * this._max);
    }

    setValue(v, force = false) {
        if (this._timeout && !force)  // user is still moving it
            return;
        this._updating = true;
        this.slider.value = Math.max(0, Math.min(this._max, v)) / this._max;
        this._value.text = `${Math.round(v)}`;
        this._updating = false;
    }
});

// Inline HSV color picker shown inside a lights submenu.
const ColorPicker = class {
    constructor(onPreview, onApply) {
        this._onPreview = onPreview;
        this._onApply = onApply;
        this.section = new PopupMenu.PopupMenuSection();
        this.section.actor.add_style_class_name('g560-picker');
        this._exact = null;  // exact hex typed/seeded; cleared when a slider moves
        const live = () => {
            this._exact = null;
            this._sync(true);
        };
        const send = () => this._onPreview(this.color);
        this._h = new SliderItem(null, 'Hue', send, {max: 360, onLive: live});
        this._s = new SliderItem(null, 'Saturation', send, {onLive: live});
        this._v = new SliderItem(null, 'Brightness', send, {onLive: live});
        for (const it of [this._h, this._s, this._v])
            this.section.addMenuItem(it);

        const row = new PopupMenu.PopupBaseMenuItem({reactive: false, can_focus: false});
        const box = new St.BoxLayout({x_expand: true, style_class: 'g560-picker-row'});
        this._preview = new St.Widget({style_class: 'g560-color-preview', y_align: Clutter.ActorAlign.CENTER});
        this._entry = new St.Entry({style_class: 'g560-hex-entry', hint_text: '#rrggbb',
            can_focus: true, x_expand: true, y_align: Clutter.ActorAlign.CENTER});
        this._entry.clutter_text.connect('activate', () => this._fromEntry());
        const apply = new St.Button({label: 'Apply', style_class: 'button g560-apply',
            can_focus: true, y_align: Clutter.ActorAlign.CENTER});
        apply.connect('clicked', () => {
            this._fromEntry(false);
            this._onApply(this.color);
        });
        box.add_child(this._preview);
        box.add_child(this._entry);
        box.add_child(apply);
        row.add_child(box);
        this.section.addMenuItem(row);
        this.hide();
    }

    get color() {
        return this._exact ?? hsvToHex(this._h.value, this._s.value, this._v.value);
    }

    get visible() {
        return this.section.actor.visible;
    }

    show(hex) {
        this.setColor(hex);
        this.section.actor.show();
    }

    hide() {
        this.section.actor.hide();
    }

    setColor(hex) {
        const hsv = hexToHsv(hex) ?? [0, 0, 100];
        this._h.setValue(Math.round(hsv[0]), true);
        this._s.setValue(Math.round(hsv[1]), true);
        this._v.setValue(Math.round(hsv[2]), true);
        this._exact = normalizeHex(hex);
        this._sync(true);
    }

    _sync(updateEntry) {
        const c = this.color;
        this._preview.style = `background-color: ${c};`;
        if (updateEntry)
            this._entry.text = c;
    }

    _fromEntry(preview = true) {
        const hex = normalizeHex(this._entry.text);
        if (!hex) {
            this._entry.text = this.color;
            return;
        }
        this.setColor(hex);
        if (preview)
            this._onPreview(hex);
    }
};

const G560Indicator = GObject.registerClass(
class G560Indicator extends PanelMenu.Button {
    _init(ext) {
        super._init(0.5, 'G560 Speakers');
        this._ext = ext;
        this._state = null;
        this._structure = '';
        this.add_child(new St.Icon({icon_name: ICON, style_class: 'system-status-icon'}));

        this._title = new PopupMenu.PopupMenuItem('G560 Speakers', {reactive: false});
        this.menu.addMenuItem(this._title);

        this._sub = new SliderItem('audio-volume-high-symbolic', 'Subwoofer',
            v => this._client.setSubwoofer(v));
        this._bright = new SliderItem('display-brightness-symbolic', 'Light brightness',
            v => this._client.setBrightness(v));
        this.menu.addMenuItem(this._sub);
        this.menu.addMenuItem(this._bright);
        this.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());

        this._link = new PopupMenu.PopupSwitchMenuItem('Link front and rear lights', true);
        this._link.connect('toggled', (_i, on) =>
            this._client.setOption('link_zones', GLib.Variant.new_boolean(on)));
        this.menu.addMenuItem(this._link);

        this._dynamic = new PopupMenu.PopupMenuSection();
        this.menu.addMenuItem(this._dynamic);

        this.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());
        this.menu.addAction('Settings…', () => this._ext.openPreferences());

        this.connect('scroll-event', (_a, event) => this._onScroll(event));
        this.menu.connect('open-state-changed', (_m, open) => {
            if (!open)
                this._groups?.forEach(g => g.picker.hide());
        });

        this._client = new DaemonClient(st => this._setState(st));
    }

    _onScroll(event) {
        if (!this._state?.connected)
            return Clutter.EVENT_PROPAGATE;
        const step = this._state.scroll_step ?? 5;
        let delta = 0;
        switch (event.get_scroll_direction()) {
        case Clutter.ScrollDirection.UP: delta = step; break;
        case Clutter.ScrollDirection.DOWN: delta = -step; break;
        case Clutter.ScrollDirection.SMOOTH: {
            const [, dy] = event.get_scroll_delta();
            this._smooth = (this._smooth ?? 0) - dy * step;
            delta = Math.trunc(this._smooth);
            this._smooth -= delta;
            break;
        }
        }
        if (delta) {
            const next = Math.max(0, Math.min(100, this._state.subwoofer + delta));
            this._state.subwoofer = next;
            this._client.setSubwoofer(next);
            this._showOsd(next);
        }
        return Clutter.EVENT_STOP;
    }

    _showOsd(level) {
        const icon = Gio.ThemedIcon.new(ICON);
        const osd = Main.osdWindowManager;
        if (osd.showAll)
            osd.showAll(icon, 'Subwoofer', level / 100, 1);
        else
            osd.show(-1, icon, 'Subwoofer', level / 100, 1);
    }

    _setState(st) {
        this._state = st;
        const ok = !!st?.connected;
        this._title.label.text = !st ? 'G560 – service not running'
            : ok ? 'G560 Speakers' : 'G560 – speaker not connected';
        for (const item of [this._sub, this._bright, this._link])
            item.setSensitive(ok);
        this.opacity = ok ? 255 : 128;
        if (!st)
            return;
        this._sub.setValue(st.subwoofer);
        this._bright.setValue(st.brightness);
        this._link.setToggleState(st.link_zones);

        const structure = JSON.stringify([st.link_zones, Object.keys(st.presets), st.swatches, ok]);
        if (structure !== this._structure) {
            this._structure = structure;
            this._buildDynamic(st, ok);
        }
        this._updateOrnaments(st);
        this._refreshRecent(st);
    }

    _buildDynamic(st, ok) {
        this._dynamic.removeAll();
        this._groups = [];
        const groups = st.link_zones ? [['all', 'Lights', 'front-left']]
            : [['front', 'Front lights', 'front-left'], ['rear', 'Rear lights', 'rear-left']];
        for (const [target, label, sample] of groups) {
            const sm = new PopupMenu.PopupSubMenuMenuItem(label, true);
            sm.icon.icon_name = 'color-select-symbolic';
            sm.setSensitive(ok);
            const items = {};
            for (const [id, name] of EFFECTS) {
                const it = sm.menu.addAction(name, () => this._client.setZone(target, {effect: id}));
                items[id] = it;
            }
            const sw = this._swatchRow(sm, target, sample);
            this._dynamic.addMenuItem(sm);
            this._groups.push({sm, items, ...sw});
        }

        const eq = new PopupMenu.PopupSubMenuMenuItem('Sound profile', true);
        eq.icon.icon_name = 'multimedia-equalizer-symbolic';
        eq.setSensitive(ok);
        this._eqItems = {};
        for (const name of Object.keys(st.presets))
            this._eqItems[name] = eq.menu.addAction(name, () => this._client.setEqPreset(name));
        this._eq = eq;
        this._dynamic.addMenuItem(eq);
    }

    _applyColor(target, sample, color) {
        const eff = this._state?.zones?.[sample]?.effect;
        const keep = eff === 'breathe' ? 'breathe' : 'solid';
        this._client.setZone(target, {effect: keep, color});
    }

    _swatchButton(color, onClick) {
        const b = new St.Button({style_class: 'g560-swatch', can_focus: true,
            style: `background-color: ${color};`, accessible_name: color});
        b.connect('clicked', onClick);
        return b;
    }

    _swatchRow(sm, target, sample) {
        const item = new PopupMenu.PopupBaseMenuItem({reactive: false, can_focus: false});
        const rows = new St.BoxLayout({orientation: Clutter.Orientation.VERTICAL, x_expand: true, style_class: 'g560-swatch-rows'});
        const box = new St.BoxLayout({x_expand: true, style_class: 'g560-swatches'});
        for (const color of this._state?.swatches ?? SWATCHES)
            box.add_child(this._swatchButton(color, () => this._applyColor(target, sample, color)));

        const picker = new ColorPicker(
            color => this._applyColor(target, sample, color),
            color => {
                this._applyColor(target, sample, color);
                this._client.addRecentColor(color);
                picker.hide();
            });
        const add = new St.Button({style_class: 'g560-swatch g560-swatch-add', can_focus: true,
            accessible_name: 'Custom color', child: new St.Icon({icon_name: 'list-add-symbolic',
                style_class: 'g560-swatch-add-icon'})});
        add.connect('clicked', () => {
            if (picker.visible)
                picker.hide();
            else
                picker.show(this._state?.zones?.[sample]?.color ?? '#ffffff');
        });
        box.add_child(add);
        rows.add_child(box);

        const recent = new St.BoxLayout({x_expand: true, style_class: 'g560-swatches'});
        rows.add_child(recent);
        item.add_child(rows);
        sm.menu.addMenuItem(item);
        sm.menu.addMenuItem(picker.section);
        return {picker, recent, target, sample, recentKey: null};
    }

    _refreshRecent(st) {
        for (const g of this._groups ?? []) {
            const colors = st.recent_colors ?? [];
            const key = colors.join(',');
            if (key === g.recentKey)
                continue;
            g.recentKey = key;
            g.recent.destroy_all_children();
            for (const color of colors) {
                g.recent.add_child(this._swatchButton(color,
                    () => this._applyColor(g.target, g.sample, color)));
            }
            g.recent.visible = colors.length > 0;
        }
    }

    _updateOrnaments(st) {
        for (const {sm, items, sample} of this._groups ?? []) {
            const z = st.zones[sample];
            for (const [id, it] of Object.entries(items))
                it.setOrnament(id === z.effect ? PopupMenu.Ornament.CHECK : PopupMenu.Ornament.NONE);
            const name = EFFECTS.find(e => e[0] === z.effect)?.[1] ?? z.effect;
            sm.label.text = `${sm.label.text.split(':')[0]}: ${name}`;
        }
        for (const [name, it] of Object.entries(this._eqItems ?? {}))
            it.setOrnament(name === st.eq_preset ? PopupMenu.Ornament.CHECK : PopupMenu.Ornament.NONE);
        if (this._eq)
            this._eq.label.text = `Sound profile: ${st.eq_preset || 'Custom'}`;
    }

    destroy() {
        this._client?.destroy();
        this._client = null;
        super.destroy();
    }
});

export default class G560Extension extends Extension {
    enable() {
        this._indicator = new G560Indicator(this);
        Main.panel.addToStatusArea(this.uuid, this._indicator);
    }

    disable() {
        this._indicator?.destroy();
        this._indicator = null;
    }
}
