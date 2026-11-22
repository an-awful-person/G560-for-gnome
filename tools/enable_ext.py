"""Add (or with --remove, drop) an extension UUID in org.gnome.shell enabled-extensions.

Works before GNOME Shell has seen the extension, unlike `gnome-extensions enable`.
"""
import ast
import subprocess
import sys

args = [a for a in sys.argv[1:] if not a.startswith("--")]
remove = "--remove" in sys.argv
uuid = args[0]

cur = subprocess.run(["gsettings", "get", "org.gnome.shell", "enabled-extensions"],
                     capture_output=True, text=True, check=True).stdout.strip()
lst = [] if cur.startswith("@as") else ast.literal_eval(cur)
if remove and uuid in lst:
    lst.remove(uuid)
elif not remove and uuid not in lst:
    lst.append(uuid)
else:
    sys.exit(0)
subprocess.run(["gsettings", "set", "org.gnome.shell", "enabled-extensions", str(lst)], check=True)
print(f"{uuid} {'removed from' if remove else 'added to'} enabled extensions")
