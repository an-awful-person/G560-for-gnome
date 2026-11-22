// Shared D-Bus client for org.g560.Daemon (used by extension.js and prefs.js).
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';

export const BUS_NAME = 'org.g560.Daemon';
export const OBJ_PATH = '/org/g560/Daemon';
export const IFACE = 'org.g560.Daemon';

export class DaemonClient {
    constructor(onState) {
        this._onState = onState;
        this._bus = Gio.DBus.session;
        this._sigId = this._bus.signal_subscribe(
            BUS_NAME, IFACE, 'StateChanged', OBJ_PATH, null, Gio.DBusSignalFlags.NONE,
            (_c, _s, _p, _i, _n, params) => this._emit(params.deep_unpack()[0]));
        this._watchId = Gio.bus_watch_name(
            Gio.BusType.SESSION, BUS_NAME, Gio.BusNameWatcherFlags.NONE,
            () => this.refresh(),
            () => this._onState(null));
    }

    _emit(json) {
        try {
            this._onState(JSON.parse(json));
        } catch (e) {
            console.error(`g560: bad state: ${e}`);
        }
    }

    refresh() {
        this.call('GetState').then(v => {
            if (v)
                this._emit(v.deep_unpack()[0]);
        });
    }

    call(method, signature = null, args = []) {
        const params = signature ? new GLib.Variant(signature, args) : null;
        return new Promise(resolve => {
            this._bus.call(BUS_NAME, OBJ_PATH, IFACE, method, params, null,
                Gio.DBusCallFlags.NONE, 5000, null, (conn, res) => {
                    try {
                        resolve(conn.call_finish(res));
                    } catch (e) {
                        console.warn(`g560: ${method} failed: ${e.message}`);
                        resolve(null);
                    }
                });
        });
    }

    setSubwoofer(v) { return this.call('SetSubwoofer', '(i)', [Math.round(v)]); }
    adjustSubwoofer(d) { return this.call('AdjustSubwoofer', '(i)', [Math.round(d)]); }
    setBrightness(v) { return this.call('SetBrightness', '(i)', [Math.round(v)]); }
    setZone(target, effect) { return this.call('SetZone', '(ss)', [target, JSON.stringify(effect)]); }
    setEqPreset(name) { return this.call('SetEqPreset', '(s)', [name]); }
    setEq(gains) { return this.call('SetEq', '(ai)', [gains.map(Math.round)]); }
    savePreset(name, gains) { return this.call('SavePreset', '(sai)', [name, gains.map(Math.round)]); }
    deletePreset(name) { return this.call('DeletePreset', '(s)', [name]); }
    setOption(key, variant) { return this.call('SetOption', '(sv)', [key, variant]); }
    setStartupAnimation(on) { return this.call('SetStartupAnimation', '(b)', [on]); }
    saveToSpeaker() { return this.call('SaveToSpeaker'); }
    addRecentColor(color) { return this.call('AddRecentColor', '(s)', [color]); }

    destroy() {
        if (this._sigId)
            this._bus.signal_unsubscribe(this._sigId);
        if (this._watchId)
            Gio.bus_unwatch_name(this._watchId);
        this._sigId = this._watchId = 0;
    }
}

export const EFFECTS = [
    ['solid', 'Solid'],
    ['breathe', 'Breathing'],
    ['cycle', 'Color cycle'],
    ['visualizer', 'Audio visualizer'],
    ['off', 'Off'],
];

// Fallback only; the daemon's state.swatches is the source of truth.
export const SWATCHES = ['#ff0000', '#ff6a00', '#ffd000', '#00ff40', '#00d0ff',
    '#0040ff', '#a000ff', '#ff00a0', '#ffffff'];
