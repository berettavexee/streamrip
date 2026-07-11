#!/usr/bin/env bash
# Régénère les captures PNG de demo/ à partir des tapes vhs.
#
# vhs (v0.11.0) n'écrit pas de PNG directement — Output *.png et la commande
# Screenshot sont silencieusement ignorés. Chaque tape produit donc un .mp4
# dont on extrait une frame avec ffmpeg.
#
# Prérequis : vhs (déposé dans ~/go/bin par `go install`), ttyd, ffmpeg.
# Lance depuis n'importe où :
#   ./demo/build_demos.sh
set -euo pipefail

export PATH="$HOME/go/bin:$PATH"
cd "$(dirname "$0")/.."

for dep in vhs ttyd ffmpeg; do
    command -v "$dep" >/dev/null 2>&1 || {
        echo "Dépendance manquante : $dep" >&2
        echo "  vhs   : go install github.com/charmbracelet/vhs@latest (puis ~/go/bin dans le PATH)" >&2
        echo "  ttyd  : sudo apt install ttyd" >&2
        echo "  ffmpeg: sudo apt install ffmpeg" >&2
        exit 1
    }
done

# build <basename> <ffmpeg-seek-args...>
#   -sseof -0.3 : dernière frame (0.3 s avant la fin) ; convient aux tapes qui
#   se terminent sur l'écran voulu. Pour une frame à un instant fixe, remplace
#   par p.ex. « -ss 5 » (option d'entrée, doit précéder -i).
build() {
    local name="$1"; shift
    echo "▶ $name"
    vhs "demo/$name.tape"
    ffmpeg -v error -y "$@" -i "demo/$name.mp4" -frames:v 1 -update 1 "demo/$name.png"
    rm -f "demo/$name.mp4"
    echo "  → demo/$name.png"
}

build example_help_page -sseof -0.3
build playlist_search   -sseof -0.3
build download_album    -sseof -0.3

echo "Terminé."
