// Render the real settings window (prefs.js, connected to the running g560d) to PNGs.
// No screen capture or permission dialog: GTK renders the window offscreen.
//
//   gjs -m tools/screenshot_prefs.js [outdir]      (default: docs/screenshots)
//
// Only GetState is called on the daemon; nothing is changed on the speaker.
import Adw from 'gi://Adw';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Graphene from 'gi://Graphene';
import Gtk from 'gi://Gtk';
import System from 'system';

const here = GLib.path_get_dirname(GLib.filename_from_uri(import.meta.url)[0]);
const root = GLib.path_get_dirname(here);
const outDir = ARGV[0] ?? GLib.build_filenamev([root, 'docs', 'screenshots']);
const extDir = GLib.build_filenamev([root, 'extension', 'g560@an-awful-person.github.io']);

// prefs.js imports ExtensionPreferences from a gnome-shell resource that only exists
// inside the Extensions app; load a copy with that import replaced by a stub.
const tmp = GLib.dir_make_tmp('g560-shot-XXXXXX');
for (const name of ['dbus.js', 'colors.js']) {
    Gio.File.new_for_path(GLib.build_filenamev([extDir, name])).copy(
        Gio.File.new_for_path(GLib.build_filenamev([tmp, name])), Gio.FileCopyFlags.OVERWRITE, null, null);
}
const [, bytes] = GLib.file_get_contents(GLib.build_filenamev([extDir, 'prefs.js']));
const src = new TextDecoder().decode(bytes).replace(
    /import \{ExtensionPreferences\} from '[^']+';/, 'class ExtensionPreferences {}');
GLib.file_set_contents(GLib.build_filenamev([tmp, 'prefs.js']), src);
const {default: Prefs} = await import(`file://${tmp}/prefs.js`);

const PAGES = [['Lights', 'settings-lights.png'], ['Equalizer', 'settings-equalizer.png'],
    ['General', 'settings-general.png']];

function wait(ms) {
    return new Promise(r => GLib.timeout_add(GLib.PRIORITY_DEFAULT, ms, () => {
        r();
        return GLib.SOURCE_REMOVE;
    }));
}

function capture(win, path) {
    const w = win.get_width(), h = win.get_height();
    const snap = new Gtk.Snapshot();
    new Gtk.WidgetPaintable({widget: win}).snapshot(snap, w, h);
    const node = snap.to_node();
    const rect = new Graphene.Rect();
    rect.init(0, 0, w, h);
    win.get_renderer().render_texture(node, rect).save_to_png(path);
    print(`wrote ${path} (${w}x${h})`);
}

const app = new Adw.Application({application_id: 'io.github.an_awful_person.G560Screenshots',
    flags: Gio.ApplicationFlags.NON_UNIQUE});
let status = 0;
app.connect('activate', async () => {
    const win = new Adw.PreferencesWindow({application: app, title: 'G560 Speakers'});
    const pages = [];
    const add = win.add.bind(win);
    win.add = page => {
        pages.push(page);
        add(page);
    };
    new Prefs().fillPreferencesWindow(win);
    win.set_default_size(720, 820);
    win.present();
    GLib.mkdir_with_parents(outDir, 0o755);
    try {
        await wait(1500);  // let GetState arrive and the window settle
        for (const [title, file] of PAGES) {
            const page = pages.find(p => p.title === title);
            if (!page)
                throw new Error(`no settings page titled ${title}`);
            win.set_visible_page(page);
            await wait(400);
            win.set_focus(null);  // no focus ring / selected text in the image
            await wait(200);
            capture(win, GLib.build_filenamev([outDir, file]));
        }
    } catch (e) {
        printerr(e);
        status = 1;
    }
    app.quit();
});
await app.runAsync([]);
System.exit(status);
