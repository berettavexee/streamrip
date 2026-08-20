#!/usr/bin/env bash
# Régénère les captures PNG de demo/ à partir des tapes vhs.
#
# vhs (v0.11.0) n'écrit pas de PNG directement (Output *.png et Screenshot sont
# rejetés par le parseur). On produit donc un intermédiaire dont ffmpeg extrait
# une frame. On utilise un GIF (et non un MP4) : le GIF n'a ni sous-échantillon-
# nage de chrominance ni compression DCT, donc le texte — surtout coloré — reste
# net (pas de bavure sur les codes couleur). Les tapes rendent aussi à haute
# résolution : la frame est nette même réduite à l'affichage (suréchantillon).
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

# Prévol : les tapes activent .venv puis appellent « rip ». Si ce binaire est
# absent du venv, la commande retomberait sur un « rip » global (ou sur rien) et
# la capture figerait une traceback au lieu de l'écran voulu — panne silencieuse
# déjà observée. On échoue franchement, ici et maintenant.
if [[ ! -x .venv/bin/rip ]]; then
    echo "Binaire introuvable : .venv/bin/rip" >&2
    echo "  Installe le paquet dans le venv d'abord : poetry sync" >&2
    exit 1
fi
if ! .venv/bin/rip --version >/dev/null 2>&1; then
    echo ".venv/bin/rip ne s'exécute pas (venv cassé ?) — régénération annulée." >&2
    exit 1
fi

# build <basename> <ffmpeg-seek-args...>
#   -sseof -0.3 : dernière frame (0.3 s avant la fin) ; convient aux tapes qui
#   se terminent sur l'écran voulu. Pour une frame à un instant fixe, remplace
#   par p.ex. « -ss 5 » (option d'entrée, doit précéder -i).
#
# Chaque tape doit faire « Output demo/<name>.gif » (cf. en-tête du fichier).
build() {
    local name="$1"; shift
    echo "▶ $name"
    vhs "demo/$name.tape"
    ffmpeg -v error -y "$@" -i "demo/$name.gif" -frames:v 1 -update 1 "demo/$name.png"
    rm -f "demo/$name.gif"
    echo "  → demo/$name.png"
}

build example_help_page -sseof -0.3
build playlist_search   -sseof -0.3
build download_album    -sseof -0.3

echo "Terminé."
