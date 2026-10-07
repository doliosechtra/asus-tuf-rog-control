#!/bin/bash
# Install ASUS TUF/ROG Control (user-local, no root needed).
# Creates ~/.local/bin/asus-tuf-rog-control + KDE launcher entry.
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
APP="$HERE/app/main.py"

python3 -c "import PySide6" 2>/dev/null || {
  echo "PySide6 not found. Install it first, e.g.: sudo pacman -S pyside6"
  exit 1
}
command -v asusctl >/dev/null || echo "warning: asusctl not found (asus-linux stack recommended)"
command -v supergfxctl >/dev/null || echo "warning: supergfxctl not found (hybrid graphics switching unavailable)"

mkdir -p ~/.local/bin ~/.local/share/applications
printf '#!/bin/bash\nexec python3 "%s" "$@"\n' "$APP" > ~/.local/bin/asus-tuf-rog-control
chmod +x ~/.local/bin/asus-tuf-rog-control
cat > ~/.local/share/applications/asus-tuf-rog-control.desktop <<EOF
[Desktop Entry]
Version=1.0
Type=Application
Name=ASUS TUF/ROG Control
Comment=Fan profiles, graphics switching and battery limit for ASUS laptops
Exec=$HOME/.local/bin/asus-tuf-rog-control
Icon=computer
Terminal=false
Categories=System;
EOF
update-desktop-database ~/.local/share/applications 2>/dev/null || true
echo "Installed. Run: asus-tuf-rog-control"
