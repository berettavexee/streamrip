#!/usr/bin/env bash
#
# run_realworld_tests.sh — Exécute streamrip sur de vrais cas d'usage Deezer/Last.fm
# et collecte les logs pour validation et diagnostic.
#
# Le script est versionné ; sa CONFIGURATION ne l'est pas. Les URLs et l'ARL du
# compte gratuit vivent dans test_urls.env (gitignoré, cf. test_urls.env.example),
# et les sorties dans realworld_runs/ (gitignoré aussi). Ne jamais forcer l'ajout
# de l'un ou de l'autre : ils portent des identifiants de compte et un secret.
#
# Le script utilise la config streamrip existante (~/.config/streamrip/config.toml),
# donc tes vrais identifiants. Il n'affiche ni n'écrit aucun secret lui-même, et
# tout fichier qu'il dépose dans realworld_runs/ passe par _scrub_secrets(), qui
# masque les valeurs des clés sensibles (arl, tokens, mot de passe, e-mail) ainsi
# que les longues chaînes hexadécimales au format ARL.
#
# Chaque cas tourne isolément : son propre dossier de téléchargement + son log
# DEBUG (rip -v -l). Un cas qui échoue n'interrompt pas les autres. Un résumé
# PASS/WARN/FAIL/SKIP est affiché à la fin.
#
# ── Renseigner les URLs ──────────────────────────────────────────────────────
# Ne devine PAS d'IDs : fournis tes propres URLs, soit par variables d'env, soit
# via un fichier test_urls.env à côté de ce script. Partir du modèle fourni :
#
#   cp test_urls.env.example test_urls.env && chmod 600 test_urls.env
#
#   TRACK_URL="https://www.deezer.com/fr/track/XXXXXXX"
#   ALBUM_URL="https://www.deezer.com/fr/album/XXXXXXX"
#   PLAYLIST_URL="https://www.deezer.com/fr/playlist/XXXXXXX"
#   LOVED_URL="https://www.deezer.com/fr/profile/XXXXXXX/loved"
#   ARTIST_TOP_URL="https://www.deezer.com/fr/artist/XXXXXXX/top_track"
#   LASTFM_URL="https://www.last.fm/user/XXXX/loved"
#   TRACK_URLS="<url1> <url2> ..."   # plusieurs tracks en une seule commande
#
# Un cas dont l'URL est vide est marqué SKIP.
#
# Deux cas ont besoin d'une URL de TRACK et retombent sur TRACK_URL si elles ne
# sont pas définies séparément — sans TRACK_URL, ils sont donc SKIP :
#
#   CONVERT_URL       conversion + re-tag après conversion
#   REPAIR_TRACK_URL  commande `rip repair`
#
# ── Passe « compte gratuit » (validation du downgrade) ───────────────────────
# Si FREE_ARL est renseigné (ARL d'un compte gratuit Deezer), le script rejoue
# TOUS les cas une seconde fois avec cet ARL et une qualité FLAC forcée
# (FREE_QUALITY, défaut 2). Un compte gratuit n'a pas de licence FLAC : on
# attend donc un downgrade transparent en MP3 par la boucle WrongLicense. C'est
# la divergence n°1 du fork vs upstream (qui plafonne sur les FILESIZE), et le
# seul moyen de la valider sur données réelles.
#
# La passe est PASS si les fichiers produits sont des MP3 valides (downgrade
# effectif), WARN si du FLAC est malgré tout servi (compte pas réellement
# gratuit ?).
#
#   FREE_ARL="<arl du compte gratuit>"   # dans test_urls.env ou l'env
#
# Le cas « loved tracks » fait exception : les favoris sont liés à un compte, et
# rejouer l'URL du compte principal avec l'ARL gratuit interroge un profil que
# celui-ci n'a pas le droit de lire — Deezer renvoie une liste vide sans erreur.
# Renseigner FREE_LOVED_URL avec le profil du compte gratuit pour couvrir ce cas,
# sinon il est SKIP dans la passe gratuite :
#
#   FREE_LOVED_URL="https://www.deezer.com/fr/profile/<uid gratuit>/loved"
#
# L'uid du compte gratuit se lit dans le log de la passe : « login successful ».
#
# L'ARL gratuit est injecté via une COPIE temporaire de la config (mktemp,
# chmod 600, supprimée en sortie) ciblée par --config-path : la config
# principale n'est jamais modifiée. L'ARL n'est ni affiché ni écrit en clair.
#
# ── Contrôle des métadonnées ────────────────────────────────────────────────
# Chaque cas valide le conteneur de TOUS ses fichiers, puis échantillonne les
# métadonnées : un fichier par dossier d'album, jusqu'à METADATA_SAMPLE_DIRS
# dossiers. Sans ça, seuls les deux cas de conversion vérifiaient les tags, et
# une régression de tagging sur les albums passait inaperçue.
#
# Un tag cœur manquant (titre/artiste/album) met le cas en FAIL ; une pochette
# absente ou en double le met en WARN. Les cas de conversion, eux, contrôlent
# TOUS leurs fichiers et traitent la pochette comme bloquante : c'est justement
# le round-trip de la pochette qu'ils testent (cf. la double pochette FLAC).
#
# ── Espace disque ───────────────────────────────────────────────────────────
# Un run complet pèse ~7 Go et ils s'accumulent dans realworld_runs/. Deux
# leviers :
#   KEEP_AUDIO=0   supprime l'audio d'un cas APRÈS validation (garde les logs)
#   KEEP_RUNS=N    ne conserve que les N runs les plus récents
#
# Usage :
#   ./run_realworld_tests.sh              # tous les cas renseignés, en réel
#   DRY_RUN=1 ./run_realworld_tests.sh    # résout/matche sans télécharger
#   QUALITY=1 ./run_realworld_tests.sh    # force une qualité (0..2 pour Deezer)
#   CONVERT_CODECS="MP3 FLAC" ./run_realworld_tests.sh   # codecs à convertir
#   MAX_TRACKS=0 ./run_realworld_tests.sh   # pas de plafond sur loved/top (lent)
#   FREE_ARL=... ./run_realworld_tests.sh   # ajoute la passe compte gratuit
#   KEEP_AUDIO=0 ./run_realworld_tests.sh   # valide puis supprime l'audio
#   KEEP_RUNS=3 ./run_realworld_tests.sh    # ne garde que les 3 derniers runs
#
# Le cas `rip repair` travaille sur une COPIE de la config avec ses propres
# fichiers de base : la base réelle (~/.config/streamrip/*.db) n'est pas touchée.
#
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# rip du venv si présent, sinon celui du PATH.
if [[ -x "$SCRIPT_DIR/.venv/bin/rip" ]]; then
    RIP="$SCRIPT_DIR/.venv/bin/rip"
else
    RIP="$(command -v rip || true)"
fi
if [[ -z "$RIP" ]]; then
    echo "ERREUR: binaire 'rip' introuvable (ni .venv/bin/rip ni dans le PATH)." >&2
    exit 1
fi

# Python du venv : sert aux contrôles de tags (mutagen) et à l'amorçage de la
# base des échecs pour le cas `rip repair`.
if [[ -x "$SCRIPT_DIR/.venv/bin/python" ]]; then
    PYBIN="$SCRIPT_DIR/.venv/bin/python"
else
    PYBIN="$(command -v python3 || true)"
fi

# Charge les URLs depuis test_urls.env s'il existe (surchargé par l'env réel).
if [[ -f "$SCRIPT_DIR/test_urls.env" ]]; then
    # shellcheck disable=SC1091
    source "$SCRIPT_DIR/test_urls.env"
fi

# Cas de test — laisser vide pour SKIP. Surchargés par l'env ou test_urls.env.
: "${TRACK_URL:=}"        # track Deezer chiffré → valide le déchiffrement streaming
: "${ALBUM_URL:=}"        # album → prefetch GW par lot + cache _TaskCache
: "${PLAYLIST_URL:=}"     # playlist → pipeline resolve/download
: "${LOVED_URL:=}"        # /profile/<uid>/loved → routage favorites: + pagination
: "${ARTIST_TOP_URL:=}"   # /artist/<id>/top_track → get_artist_top_tracks
: "${LASTFM_URL:=}"       # last.fm → parsers paginés refactorisés

# Ces deux cas ont besoin d'une URL de TRACK (pas d'album) : la conversion se
# vérifie fichier par fichier, et `repair` rejoue des tracks individuelles.
: "${CONVERT_URL:=$TRACK_URL}"      # conversion + re-tag (PR #1006)
: "${REPAIR_TRACK_URL:=$TRACK_URL}" # commande `rip repair` (PR #1023)

# Plusieurs tracks individuelles passées en une fois à `rip url` : couvre le
# chemin PendingSingle, distinct de PendingTrack emprunté par les albums.
: "${TRACK_URLS:=}"

# Options d'exécution.
: "${QUALITY:=}"          # vide = qualité de la config ; sinon 0..2 (Deezer)
: "${DRY_RUN:=}"          # non-vide = -n (aucun téléchargement réel)
: "${LASTFM_SOURCE:=deezer}"
# Plafond passé en --max-tracks aux cas « loved tracks » et « artist top
# tracks », qui renvoient sinon des centaines de pistes : 11 Go en 6 min et
# 3,4 Go en 2 min sur un compte réel. Le plafond s'applique à la résolution,
# donc la liste complète est quand même récupérée et la pagination GW des
# favoris — spécificité du fork — reste exercée. 0 = pas de limite.
: "${MAX_TRACKS:=50}"

# Codecs testés par le cas conversion, séparés par des espaces. Les deux par
# défaut sont les cas limites opposés :
#   AIFF — ffmpeg n'y écrit aucun tag, tout vient du re-tag de Track._convert ;
#   OPUS — tag_file() le REFUSE, donc pas de re-tag : les tags viennent de
#          ffmpeg et la pochette de _embed_cover_art du convertisseur.
: "${CONVERT_CODECS:=AIFF OPUS}"

# Passe compte gratuit (validation du downgrade WrongLicense). Vide = ignorée.
: "${FREE_ARL:=}"         # ARL d'un compte gratuit ; déclenche la 2e passe
: "${FREE_QUALITY:=2}"    # qualité forcée pour la passe gratuite (FLAC demandé)
# Les favoris sont liés à un compte : rejouer LOVED_URL avec l'ARL gratuit
# interroge les favoris du compte PRINCIPAL, que le gratuit n'a pas le droit de
# lire — Deezer renvoie alors une liste vide, sans erreur. Renseigner ici le
# profil du compte gratuit lui-même ; sinon le cas est SKIP dans cette passe.
: "${FREE_LOVED_URL:=}"

# Contrôle des métadonnées et gestion de l'espace disque.
: "${METADATA_SAMPLE_DIRS:=8}"  # nb max de dossiers échantillonnés ; 0 = tous les fichiers
: "${METADATA_SAMPLE_MIN:=5}"   # nb min de fichiers contrôlés par cas (playlists : 1 seul dossier)
: "${KEEP_AUDIO:=1}"      # 0 = supprime l'audio après validation
: "${KEEP_RUNS:=}"        # N = ne conserve que les N runs les plus récents ; vide = tous

RUN_DIR="$SCRIPT_DIR/realworld_runs/$(date +%Y%m%d-%H%M%S)"
mkdir -p "$RUN_DIR"

echo "streamrip : $RIP ($($RIP --version 2>/dev/null || echo '?'))"
echo "Sorties   : $RUN_DIR"
[[ -n "$DRY_RUN" ]] && echo "Mode      : DRY-RUN (aucun téléchargement)"
echo

# Résultats accumulés pour le tableau final.
declare -a R_LABEL R_STATUS R_DUR R_FILES R_SIZE R_NOTE

# Options CLI injectées dans chaque cas (ex. --config-path pour la passe
# gratuite) et attentes associées. Réinitialisés entre les passes.
EXTRA_OPTS=()
EXPECT_MP3=""            # non-vide = on attend un downgrade en MP3
CASE_PREFIX=""           # préfixe de label/dossier, pour distinguer les passes

# Fichiers temporaires porteurs de secrets (copies de config avec identifiants
# en clair). Un seul gestionnaire pour tous : bash ne garde qu'un handler par
# signal, donc plusieurs `trap` successifs se remplaceraient silencieusement.
declare -a _TMP_SECRET_FILES=()
_cleanup_tmp_secrets() {
    local _f
    for _f in ${_TMP_SECRET_FILES+"${_TMP_SECRET_FILES[@]}"}; do
        rm -f "$_f"
    done
    return 0
}
# INT/TERM en plus d'EXIT : ces fichiers portent un ARL, ils ne doivent pas
# survivre à un Ctrl-C.
trap _cleanup_tmp_secrets EXIT INT TERM

# Crée une copie temporaire de la config streamrip, en 0600 et HORS de
# l'arborescence du projet, et l'enregistre pour suppression en sortie. Écrit
# son chemin sur stdout.
#
# Hors de l'arborescence, parce qu'une copie de config porte les identifiants en
# clair : realworld_runs/ est certes gitignoré, mais un `git add -f`, une archive
# ou une sauvegarde du dossier suffirait à les emporter. Ce qui atterrit dans le
# dossier de run est une copie SCRUBBÉE, jamais l'originale.
#
# Le chemin sort par la globale _TMP_CONFIG et NON par stdout : un appel en
# `$(...)` s'exécuterait dans un sous-shell, où l'enregistrement dans
# _TMP_SECRET_FILES serait perdu — le fichier survivrait alors au script. Une
# globale plutôt qu'un nameref : `local -n` se résout dans la portée de la
# fonction, où une locale homonyme le masque silencieusement (déjà vu ici).
#
# Args:
#   $1 — chemin de la config source à cloner.
#
# Returns:
#   0 et le chemin du clone dans _TMP_CONFIG ; 1 si mktemp ou la copie échoue.
_TMP_CONFIG=""
_make_temp_config() {
    _TMP_CONFIG=""
    local _src="$1" _dst
    _dst=$(mktemp -t streamrip-realworld-XXXXXX.toml) || return 1
    chmod 600 "$_dst"
    cp "$_src" "$_dst" || return 1
    _TMP_SECRET_FILES+=("$_dst")
    _TMP_CONFIG="$_dst"
}

# Masque les secrets d'un fichier destiné à rester dans realworld_runs/ (log,
# sortie console, copie de config).
#
# Deux passes complémentaires :
#   1. par NOM DE CLÉ — une config contient bien d'autres identifiants que
#      l'ARL (e-mail Qobuz, md5 du mot de passe, jetons Tidal), qu'aucun critère
#      de forme ne distingue d'une valeur anodine ;
#   2. par FORME — les chaînes hexadécimales de 100+ caractères (format ARL)
#      croisées ailleurs que dans une affectation, typiquement au fil d'un log.
#
# Le seuil de 100 est volontairement haut : les md5 (32 caractères) sont partout
# dans les URLs de CDN Deezer et les masquer rendrait les logs inexploitables.
# Le seul md5 sensible — le mot de passe Qobuz — est attrapé par la passe 1.
_scrub_secrets() {
    local _keys='arl|email_or_userid|password_or_token|access_token|refresh_token|user_id|client_id|client_secret|password|email|token'
    sed -i -E \
        -e "s/^([[:space:]]*($_keys)[[:space:]]*=[[:space:]]*\")[^\"]+\"/\1<REDACTED>\"/I" \
        -e "s/(\b($_keys)\"?[[:space:]]*[:=][[:space:]]*\"?)[A-Za-z0-9._~+\/-]{8,}/\1<REDACTED>/gI" \
        -e 's/[0-9a-fA-F]{100,}/<REDACTED>/g' \
        "$1" 2>/dev/null || true
}

# Contrôle d'intégrité + de COHÉRENCE d'un fichier audio, via file(1) : taille
# minimale, contenu réellement décodable, ET contenu cohérent avec l'extension.
# Écrit une raison lisible sur stdout et renvoie 0 (valide) / 1 (invalide).
#
# Le contrôle de cohérence attrape deux corruptions silencieuses vues en réel,
# qu'un simple test de magic bytes laisserait passer :
#   - un flux chiffré non déchiffré (extension .flac mais contenu « data ») ;
#   - un flux FLAC encapsulé en .mp3 (file dit « ID3 … contains:FLAC »).
_check_audio_file() {
    local f="$1"
    local size ftype
    size=$(stat -c%s "$f" 2>/dev/null || echo 0)
    if (( size < 10240 )); then       # < 10 Ko => tronqué / body d'erreur
        echo "trop petit (${size}o)"; return 1
    fi
    ftype=$(file -b "$f" 2>/dev/null)
    case "${f,,}" in
        *.flac)
            if [[ "$ftype" == *"FLAC audio bitstream"* ]]; then return 0; fi
            echo "extension .flac mais contenu « ${ftype:0:40} » (chiffré/corrompu ?)"
            return 1 ;;
        *.mp3)
            # Un .mp3 dont file voit du FLAC = flux FLAC mis dans un conteneur MP3.
            if [[ "$ftype" == *"FLAC"* ]]; then
                echo "extension .mp3 mais contenu FLAC (mauvais conteneur)"; return 1
            fi
            if [[ "$ftype" == *"MPEG"* || "$ftype" == *"ID3"* || "$ftype" == *"MP3"* || "$ftype" == *"Audio file"* ]]; then
                return 0
            fi
            echo "extension .mp3 mais contenu « ${ftype:0:40} »"; return 1 ;;
        *.aiff|*.aif)
            if [[ "$ftype" == *"AIFF"* || "$ftype" == *"IFF data"* ]]; then return 0; fi
            echo "extension .aiff mais contenu « ${ftype:0:40} »"; return 1 ;;
        *.m4a)
            if [[ "$ftype" == *"MPEG-4"* || "$ftype" == *"ISO Media"* || "$ftype" == *"M4A"* ]]; then
                return 0
            fi
            echo "extension .m4a mais contenu « ${ftype:0:40} »"; return 1 ;;
        *.opus)
            if [[ "$ftype" == *"Opus"* || "$ftype" == *"Ogg data"* ]]; then return 0; fi
            echo "extension .opus mais contenu « ${ftype:0:40} »"; return 1 ;;
        *.ogg)
            if [[ "$ftype" == *"Ogg data"* || "$ftype" == *"Vorbis"* ]]; then return 0; fi
            echo "extension .ogg mais contenu « ${ftype:0:40} »"; return 1 ;;
        *)
            return 0 ;;  # autre conteneur : on ne juge que la taille
    esac
}

# Contrôle des TAGS d'un fichier audio, via mutagen.
#
# ffmpeg ne reporte pas tous les champs lors d'un changement de conteneur (il
# perd l'ISRC et les paroles), et n'écrit aucun tag du tout en AIFF. Après
# conversion, Track._convert re-tague le fichier : ce contrôle vérifie que ça a
# bien eu lieu sur un vrai téléchargement.
#
# Écrit une raison lisible sur stdout, renvoie 0 (tags présents) / 1 (manquants).
_check_audio_tags() {
    local f="$1"
    "$PYBIN" - "$f" <<'PY'
import sys
import mutagen

path = sys.argv[1]
try:
    audio = mutagen.File(path)
except Exception as exc:
    # Sans ce filet, mutagen deverse une traceback complete sur stderr au
    # milieu du run, la ou une ligne suffit : le fichier est illisible.
    print("mutagen refuse le fichier: %s" % exc.__class__.__name__)
    sys.exit(1)
if audio is None:
    print("mutagen ne sait pas lire le fichier")
    sys.exit(1)
if audio.tags is None:
    print("aucun tag (ffmpeg n'a rien ecrit et le re-tag n'a pas eu lieu)")
    sys.exit(1)

keys = {str(k).lower() for k in audio.tags.keys()}


def has(*candidates):
    return any(c in k for k in keys for c in candidates)


missing = [
    label
    for label, cands in (
        ("titre", ("tit2", "title", "\xa9nam")),
        ("artiste", ("tpe1", "artist", "\xa9art")),
        ("album", ("talb", "album", "\xa9alb")),
    )
    if not has(*cands)
]
if missing:
    print("champs manquants: " + ", ".join(missing))
    sys.exit(1)

covers = 0
if getattr(audio, "pictures", None):
    covers = len(audio.pictures)
elif "metadata_block_picture" in audio.tags:
    covers = len(audio.tags["metadata_block_picture"])
elif hasattr(audio.tags, "getall"):
    covers = len(audio.tags.getall("APIC"))
else:
    covers = len(audio.tags.get("covr", []))

# Code 2 = souple : l'appelant decide si la pochette est bloquante. Elle l'est
# pour les cas de conversion, qui testent precisement son round-trip, et ne
# l'est pas ailleurs, ou elle depend de la config artwork.
if covers == 0:
    print("aucune pochette embarquee")
    sys.exit(2)
if covers > 1:
    print(f"{covers} pochettes embarquees (doublon)")
    sys.exit(2)

print(f"{len(keys)} tags, 1 pochette")
sys.exit(0)
PY
}

# Échantillonne les fichiers dont les métadonnées seront contrôlées.
# (METADATA_SAMPLE_DIRS = 0 : pas d'échantillonnage, tous les fichiers.)
#
# Deux passes, parce que les deux formes de sortie ont des besoins opposés :
#
#   1. Un fichier par dossier, jusqu'à METADATA_SAMPLE_DIRS dossiers. Les tags
#      sont uniformes au sein d'un album : un fichier suffit à y détecter une
#      régression, et une discographie est ainsi couverte album par album.
#   2. Complément jusqu'à METADATA_SAMPLE_MIN fichiers, répartis à pas régulier
#      dans la liste. Sans lui une playlist — 59 fichiers dans UN dossier —
#      serait jugée sur un seul fichier, ce qui ne vaut guère mieux que rien.
#      Le pas évite de ne prendre que le début de la liste, souvent le même
#      album.
#
# Écrit un fichier par ligne sur stdout.
_sample_files() {
    local -n _all="$1"
    # Locaux prefixes : un nameref bash se resout dans la portee de la fonction,
    # donc un local homonyme du tableau de l'appelant l'occulterait silencieusement
    # (_all pointerait sur le local scalaire, pas sur le tableau attendu).
    local _total=${#_all[@]}
    if (( METADATA_SAMPLE_DIRS == 0 || _total == 0 )); then
        (( _total > 0 )) && printf '%s\n' "${_all[@]}"
        return
    fi

    local -A _picked=() _seen_dir=()
    local -a _sample=()
    local _n_dirs=0 _f _dir _i

    for _f in "${_all[@]}"; do
        _dir=$(dirname "$_f")
        [[ -n "${_seen_dir[$_dir]:-}" ]] && continue
        _seen_dir[$_dir]=1
        _picked[$_f]=1
        _sample+=("$_f")
        _n_dirs=$((_n_dirs+1))
        (( _n_dirs >= METADATA_SAMPLE_DIRS )) && break
    done

    local _target=$(( METADATA_SAMPLE_MIN < _total ? METADATA_SAMPLE_MIN : _total ))
    if (( ${#_sample[@]} < _target )); then
        local _stride=$(( _total / _target ))
        (( _stride < 1 )) && _stride=1
        for (( _i = 0; _i < _total && ${#_sample[@]} < _target; _i += _stride )); do
            _f="${_all[$_i]}"
            [[ -n "${_picked[$_f]:-}" ]] && continue
            _picked[$_f]=1
            _sample+=("$_f")
        done
        # Le pas peut laisser la cible inatteinte (doublons avec la 1re passe) :
        # on complete alors sequentiellement.
        for (( _i = 0; _i < _total && ${#_sample[@]} < _target; _i++ )); do
            _f="${_all[$_i]}"
            [[ -n "${_picked[$_f]:-}" ]] && continue
            _picked[$_f]=1
            _sample+=("$_f")
        done
    fi

    printf '%s\n' "${_sample[@]}"
}

# Mis à "1" par un appelant pour que run_case contrôle aussi les tags.
CASE_CHECK_TAGS=""
# Plafond --max-tracks du cas courant ; vide = pas de plafond.
CASE_MAX_TRACKS=""

# Supprime l'audio d'un cas une fois validé, en gardant tout ce qui sert au
# diagnostic (logs, console, rapports d'anomalies). Appelé après le calcul du
# statut : la validation a déjà eu lieu, l'audio ne sert plus à rien.
_discard_case_audio() {
    local dl_dir="$1"
    [[ "$KEEP_AUDIO" == "0" ]] || return 0
    rm -rf "$dl_dir"
    mkdir -p "$dl_dir"
}

run_case() {
    local label="$CASE_PREFIX$1"; shift   # le reste = sous-commande rip + args
    local safe; safe=$(echo "$label" | tr -c 'A-Za-z0-9._-' '_')
    local case_dir="$RUN_DIR/$safe"
    local dl_dir="$case_dir/downloads"
    local log="$case_dir/rip.log"
    local out="$case_dir/console.txt"
    mkdir -p "$dl_dir"

    local -a opts=(-v -l "$log" -f "$dl_dir" --no-db)
    [[ ${#EXTRA_OPTS[@]} -gt 0 ]] && opts+=("${EXTRA_OPTS[@]}")
    [[ -n "$QUALITY" ]] && opts+=(-q "$QUALITY")
    [[ -n "$CASE_MAX_TRACKS" ]] && opts+=(--max-tracks "$CASE_MAX_TRACKS")
    [[ -n "$DRY_RUN" ]] && opts+=(-n)

    echo ">>> $label"
    echo "    rip ${opts[*]} $*"
    local start; start=$(date +%s)
    "$RIP" "${opts[@]}" "$@" >"$out" 2>&1
    local rc=$?
    local dur=$(( $(date +%s) - start ))

    _scrub_secrets "$log"
    _scrub_secrets "$out"

    # Bilan des fichiers téléchargés.
    local -a files=()
    mapfile -t files < <(find "$dl_dir" -type f \
        \( -iname '*.flac' -o -iname '*.mp3' -o -iname '*.m4a' \
           -o -iname '*.aiff' -o -iname '*.aif' \
           -o -iname '*.opus' -o -iname '*.ogg' \) 2>/dev/null)
    local n=${#files[@]}
    local human_size; human_size=$(du -sh "$dl_dir" 2>/dev/null | cut -f1)

    # Détermination du statut.
    local status note=""
    if [[ -n "$DRY_RUN" ]]; then
        if (( rc == 0 )); then status="PASS"; note="dry-run ok"; else status="FAIL"; note="rc=$rc"; fi
    elif (( rc != 0 )); then
        status="FAIL"; note="rc=$rc (voir console.txt)"
    elif (( n == 0 )); then
        status="WARN"; note="0 fichier audio produit"
    else
        local bad=0 f reason
        local invalid_log="$case_dir/invalid_files.txt"
        : > "$invalid_log"
        for f in "${files[@]}"; do
            if ! reason=$(_check_audio_file "$f"); then
                bad=$((bad+1))
                printf '%s\t%s\n' "$reason" "$(basename "$f")" >> "$invalid_log"
            fi
        done
        if (( bad > 0 )); then
            status="FAIL"; note="$bad/$n invalide(s) — voir invalid_files.txt"
        else
            rm -f "$invalid_log"

            # Contrôle des tags. Les cas de conversion passent TOUS leurs
            # fichiers (ils en ont un) et traitent la pochette comme bloquante :
            # c'est son round-trip qu'ils testent. Les autres échantillonnent un
            # fichier par dossier et se contentent d'un WARN sur la pochette.
            local -a sample=()
            if [[ -n "$CASE_CHECK_TAGS" ]]; then
                sample=("${files[@]}")
            else
                mapfile -t sample < <(_sample_files files)
            fi

            local untagged=0 nocover=0 treason trc
            local tag_log="$case_dir/tag_issues.txt"
            : > "$tag_log"
            for f in "${sample[@]}"; do
                treason=$(_check_audio_tags "$f"); trc=$?
                case $trc in
                    0) ;;
                    2) nocover=$((nocover+1))
                       printf 'COVER\t%s\t%s\n' "$treason" "$(basename "$f")" >> "$tag_log" ;;
                    *) untagged=$((untagged+1))
                       printf 'TAG\t%s\t%s\n' "$treason" "$(basename "$f")" >> "$tag_log" ;;
                esac
            done

            local ns=${#sample[@]}
            if (( untagged > 0 )); then
                status="FAIL"; note="$untagged/$ns sans tags — voir tag_issues.txt"
            elif (( nocover > 0 )); then
                if [[ -n "$CASE_CHECK_TAGS" ]]; then
                    status="FAIL"; note="$nocover/$ns pochette KO — voir tag_issues.txt"
                else
                    status="WARN"; note="$nocover/$ns pochette KO — voir tag_issues.txt"
                fi
            else
                rm -f "$tag_log"
                if [[ -n "$CASE_CHECK_TAGS" ]]; then
                    status="PASS"; note="$n fichier(s) valides + tagués"
                else
                    status="PASS"; note="$n valides, $ns échantillon(s) tagué(s)"
                fi
            fi

            # Downgrade attendu (passe compte gratuit) : sans licence HiFi, la
            # boucle WrongLicense doit rabattre la qualité avant tout
            # téléchargement.
            #
            # Le verdict se lit dans le LOG, pas sur l'extension des fichiers :
            # un cas de conversion réécrit le conteneur (FLAC servi -> .aiff
            # produit), donc l'extension ne dit plus rien de ce que Deezer a
            # réellement servi. La ligne « resolved at quality N » le dit, elle,
            # et vaut pour tous les cas.
            #
            # Du FLAC servi malgré tout n'est pas une régression du fork — c'est
            # le compte qui n'est pas réellement gratuit — d'où WARN, pas FAIL.
            if [[ -n "$EXPECT_MP3" && "$status" == "PASS" ]]; then
                local n_hifi n_down
                n_hifi=$(grep -c "resolved at quality 2 (FLAC)" "$log" 2>/dev/null || true)
                n_down=$(grep -c "not available for this account" "$log" 2>/dev/null || true)
                if (( n_hifi > 0 )); then
                    status="WARN"
                    note="$n_hifi piste(s) servie(s) en FLAC — pas de downgrade (compte non gratuit ?)"
                elif (( n_down == 0 )); then
                    status="WARN"
                    note="aucun refus de licence journalisé — downgrade non exercé ?"
                else
                    note="$n fichier(s), downgrade effectif ($n_down refus de licence)"
                fi
            fi
        fi
    fi

    _discard_case_audio "$dl_dir"

    echo "    -> $status  (${dur}s, $n fichier(s), ${human_size:-0})  $note"
    echo
    R_LABEL+=("$label"); R_STATUS+=("$status"); R_DUR+=("${dur}s")
    R_FILES+=("$n"); R_SIZE+=("${human_size:-0}"); R_NOTE+=("$note")
}

skip_case() {
    local label="$CASE_PREFIX$1" reason="$2"
    echo ">>> $label"
    echo "    -> SKIP  ($reason)"
    echo
    R_LABEL+=("$label"); R_STATUS+=("SKIP"); R_DUR+=("-")
    R_FILES+=("-"); R_SIZE+=("-"); R_NOTE+=("$reason")
}

# Cas `rip repair` : amorce une base des échecs isolée avec une vraie track,
# puis vérifie que repair la retélécharge ET nettoie la ligne correspondante.
#
# Tourne sur une COPIE de la config, avec ses propres fichiers de base : la base
# réelle de l'utilisateur (~/.config/streamrip/*.db) n'est jamais touchée. Ce cas
# ne peut pas utiliser --no-db, puisque c'est justement la base qu'il teste.
run_repair_case() {
    local label="rip repair (base des échecs)"
    local url="$1"

    if [[ -z "$url" ]]; then
        skip_case "$label" "REPAIR_TRACK_URL/TRACK_URL non renseignée"; return
    fi
    if [[ -n "$DRY_RUN" ]]; then
        skip_case "$label" "incompatible avec DRY_RUN (repair télécharge)"; return
    fi
    local track_id
    track_id=$(sed -E 's#.*/track/([0-9]+).*#\1#' <<<"$url")
    if ! [[ "$track_id" =~ ^[0-9]+$ ]]; then
        skip_case "$label" "pas d'id de track dans l'URL ($url)"; return
    fi

    local case_dir="$RUN_DIR/rip_repair"
    local dl_dir="$case_dir/downloads"
    local log="$case_dir/rip.log"
    local out="$case_dir/console.txt"
    local failed_db="$case_dir/failed_downloads.db"
    local downloads_db="$case_dir/downloads.db"
    mkdir -p "$dl_dir"

    local src_cfg
    src_cfg=$("$PYBIN" -c 'from streamrip.config import DEFAULT_CONFIG_PATH; print(DEFAULT_CONFIG_PATH)' 2>/dev/null)
    if [[ ! -f "$src_cfg" ]]; then
        skip_case "$label" "config introuvable ($src_cfg)"; return
    fi
    # La copie vit hors de l'arborescence du projet : elle porte l'ARL en clair
    # le temps du run, et seule une version scrubbée rejoint le dossier de run.
    if ! _make_temp_config "$src_cfg"; then
        skip_case "$label" "impossible de préparer la config temporaire"; return
    fi
    local cfg="$_TMP_CONFIG"
    sed -i -E "s#^downloads_path = .*#downloads_path = \"$downloads_db\"#" "$cfg"
    sed -i -E "s#^failed_downloads_path = .*#failed_downloads_path = \"$failed_db\"#" "$cfg"

    # Amorçage : la track est déclarée comme ayant échoué précédemment.
    "$PYBIN" - "$failed_db" "$track_id" <<'PY'
import sys
from streamrip import db
db.Failed(sys.argv[1]).add(("deezer", "track", sys.argv[2]))
PY

    echo ">>> $label"
    echo "    amorçage: failed_downloads = [(deezer, track, $track_id)]"
    local start; start=$(date +%s)
    "$RIP" --config-path "$cfg" -v -l "$log" -f "$dl_dir" repair -y >"$out" 2>&1
    local rc=$?
    local dur=$(( $(date +%s) - start ))

    _scrub_secrets "$log"
    _scrub_secrets "$out"
    # La config exacte qui a servi est utile au diagnostic ; on en dépose une
    # copie scrubbée et on se débarrasse de l'originale sans attendre la sortie.
    cp "$cfg" "$case_dir/config.toml" && _scrub_secrets "$case_dir/config.toml"
    rm -f "$cfg"

    local -a files=()
    mapfile -t files < <(find "$dl_dir" -type f \
        \( -iname '*.flac' -o -iname '*.mp3' -o -iname '*.m4a' \
           -o -iname '*.aiff' -o -iname '*.aif' \
           -o -iname '*.opus' -o -iname '*.ogg' \) 2>/dev/null)
    local n=${#files[@]}
    local human_size; human_size=$(du -sh "$dl_dir" 2>/dev/null | cut -f1)

    # Reste-t-il des lignes dans la base des échecs ?
    local remaining
    remaining=$("$PYBIN" - "$failed_db" <<'PY'
import sys
from streamrip import db
print(len(db.Failed(sys.argv[1]).all()))
PY
)

    local status note=""
    if (( rc != 0 )); then
        status="FAIL"; note="rc=$rc (voir console.txt)"
    elif (( n == 0 )); then
        status="FAIL"; note="repair n'a produit aucun fichier"
    elif [[ "$remaining" != "0" ]]; then
        status="FAIL"; note="track téléchargée mais toujours listée en échec ($remaining restante(s))"
    else
        local bad=0 f reason
        for f in "${files[@]}"; do
            reason=$(_check_audio_file "$f") || bad=$((bad+1))
        done
        if (( bad > 0 )); then
            status="FAIL"; note="$bad/$n fichier(s) invalide(s)"
        else
            status="PASS"; note="track réparée, base des échecs vidée"
        fi
    fi

    _discard_case_audio "$dl_dir"

    echo "    -> $status  (${dur}s, $n fichier(s), ${human_size:-0})  $note"
    echo
    R_LABEL+=("$label"); R_STATUS+=("$status"); R_DUR+=("${dur}s")
    R_FILES+=("$n"); R_SIZE+=("${human_size:-0}"); R_NOTE+=("$note")
}

# ── Exécution des cas ────────────────────────────────────────────────────────
run_or_skip() {
    local label="$1" url="$2"; shift 2   # reste = args rip (sous-commande + url placé)
    if [[ -z "$url" ]]; then
        skip_case "$label" "URL non renseignée"
    else
        run_case "$label" "$@"
    fi
}

# Tous les cas, dans une fonction pour pouvoir les rejouer avec un autre compte
# (cf. passe gratuite plus bas). L'état qui différencie les passes — EXTRA_OPTS,
# EXPECT_MP3, CASE_PREFIX — est posé par l'appelant.
run_all_cases() {
    run_or_skip "Deezer track (déchiffrement streaming)" "$TRACK_URL"      url "$TRACK_URL"
    run_or_skip "Deezer album (prefetch GW + cache)"     "$ALBUM_URL"      url "$ALBUM_URL"
    run_or_skip "Deezer playlist (pipeline resolve/dl)"  "$PLAYLIST_URL"   url "$PLAYLIST_URL"
    # Cas lourds : plafonnés pour que le script reste utilisable (cf. MAX_TRACKS).
    [[ "$MAX_TRACKS" != "0" ]] && CASE_MAX_TRACKS="$MAX_TRACKS"
    # Seule URL du jeu liée à un compte Deezer : les playlists, albums et
    # artistes sont publics, et le cas Last.fm ne se sert du compte que comme
    # source de téléchargement.
    if [[ -n "$EXPECT_MP3" ]]; then
        if [[ -n "$FREE_LOVED_URL" ]]; then
            run_case "Deezer loved tracks (favorites:)" url "$FREE_LOVED_URL"
        else
            skip_case "Deezer loved tracks (favorites:)" \
                "favoris liés au compte principal — renseigner FREE_LOVED_URL"
        fi
    else
        run_or_skip "Deezer loved tracks (favorites:)"   "$LOVED_URL"      url "$LOVED_URL"
    fi
    run_or_skip "Deezer artist top tracks"               "$ARTIST_TOP_URL" url "$ARTIST_TOP_URL"
    CASE_MAX_TRACKS=""
    run_or_skip "Last.fm playlist (parsers paginés)"     "$LASTFM_URL"     lastfm -s "$LASTFM_SOURCE" "$LASTFM_URL"

    # Tracks individuelles : chemin PendingSingle (≠ PendingTrack des albums).
    # shellcheck disable=SC2086 — TRACK_URLS est volontairement découpé en mots.
    if [[ -z "$TRACK_URLS" ]]; then
        skip_case "Deezer tracks individuelles (PendingSingle)" "TRACK_URLS non renseignée"
    else
        run_case "Deezer tracks individuelles (PendingSingle)" url $TRACK_URLS
    fi

    # Conversion : tous les fichiers sont contrôlés, pochette comprise.
    for _codec in $CONVERT_CODECS; do
        CASE_CHECK_TAGS=1
        run_or_skip "Conversion $_codec (tags + pochette)" "$CONVERT_URL" \
            -c "$_codec" url "$CONVERT_URL"
        CASE_CHECK_TAGS=""
    done
}

run_all_cases

# `repair` teste la base des échecs, pas le licensing : il tourne une seule fois,
# et sur sa propre copie de config (donc hors passe gratuite).
run_repair_case "$REPAIR_TRACK_URL"

# ── Passe compte gratuit ─────────────────────────────────────────────────────
# Rejoue tout avec l'ARL d'un compte gratuit et FLAC forcé : sans licence HiFi,
# la boucle WrongLicense doit dégrader en MP3 de façon transparente. C'est la
# divergence n°1 du fork vs upstream, et elle n'a aucune autre couverture réelle.
FREE_CFG=""

# Clone la config principale en substituant l'ARL, via _make_temp_config : le
# fichier est en 0600, hors de l'arborescence du projet, et supprimé en sortie
# (y compris sur Ctrl-C). La config principale n'est jamais modifiée.
_make_free_config() {
    local src
    src=$("$PYBIN" -c 'from streamrip.config import DEFAULT_CONFIG_PATH; print(DEFAULT_CONFIG_PATH)' 2>/dev/null)
    [[ -f "$src" ]] || return 1
    _make_temp_config "$src" || return 1
    FREE_CFG="$_TMP_CONFIG"
    # L'ARL est passé par un fichier plutôt qu'inline : un sed avec la valeur
    # dans la ligne de commande l'exposerait dans la table des processus.
    ARL_VALUE="$FREE_ARL" "$PYBIN" - "$FREE_CFG" <<'PY'
import os
import re
import sys

path = sys.argv[1]
with open(path, encoding="utf-8") as fh:
    text = fh.read()
new, n = re.subn(
    r'^arl = ".*"$',
    'arl = "%s"' % os.environ["ARL_VALUE"],
    text,
    count=1,
    flags=re.MULTILINE,
)
if n != 1:
    sys.exit("aucune ligne arl = ... trouvee dans la config")
with open(path, "w", encoding="utf-8") as fh:
    fh.write(new)
PY
}

if [[ -n "$FREE_ARL" ]]; then
    if [[ -n "$DRY_RUN" ]]; then
        echo "Passe compte gratuit ignorée (DRY_RUN : rien n'est téléchargé, donc"
        echo "rien à contrôler sur le format servi)."
        echo
    elif ! _make_free_config; then
        echo "Passe compte gratuit ignorée : impossible de préparer la config temporaire." >&2
        echo
    else
        echo "════════════════════════════════════════════════════════════════════════"
        echo "PASSE COMPTE GRATUIT — qualité $FREE_QUALITY forcée, downgrade MP3 attendu"
        echo "════════════════════════════════════════════════════════════════════════"
        echo
        EXTRA_OPTS=(--config-path "$FREE_CFG")
        EXPECT_MP3=1
        CASE_PREFIX="[gratuit] "
        QUALITY="$FREE_QUALITY"
        run_all_cases
        EXTRA_OPTS=(); EXPECT_MP3=""; CASE_PREFIX=""
    fi
fi

# ── Résumé ───────────────────────────────────────────────────────────────────
echo "════════════════════════════════════════════════════════════════════════"
echo "RÉSUMÉ"
echo "════════════════════════════════════════════════════════════════════════"
printf '%-52s %-6s %-6s %-7s %-6s %s\n' "CAS" "STATUT" "DURÉE" "FICH." "TAILLE" "NOTE"
fail=0 warn=0 pass=0 skip=0
for i in "${!R_LABEL[@]}"; do
    printf '%-52s %-6s %-6s %-7s %-6s %s\n' \
        "${R_LABEL[$i]:0:52}" "${R_STATUS[$i]}" "${R_DUR[$i]}" \
        "${R_FILES[$i]}" "${R_SIZE[$i]}" "${R_NOTE[$i]}"
    case "${R_STATUS[$i]}" in
        PASS) pass=$((pass+1)) ;; WARN) warn=$((warn+1)) ;;
        FAIL) fail=$((fail+1)) ;; SKIP) skip=$((skip+1)) ;;
    esac
done
echo "────────────────────────────────────────────────────────────────────────"
echo "PASS=$pass  WARN=$warn  FAIL=$fail  SKIP=$skip"
echo "Logs et téléchargements : $RUN_DIR"
[[ "$KEEP_AUDIO" == "0" ]] && echo "(audio supprimé après validation — KEEP_AUDIO=0)"

# Purge des runs anciens. Après le résumé : un échec de purge ne doit pas
# masquer le résultat des tests, et le run courant compte dans les N gardés.
if [[ -n "$KEEP_RUNS" ]]; then
    if [[ "$KEEP_RUNS" =~ ^[0-9]+$ ]] && (( KEEP_RUNS > 0 )); then
        mapfile -t _old_runs < <(
            find "$SCRIPT_DIR/realworld_runs" -mindepth 1 -maxdepth 1 -type d \
                | sort -r | tail -n "+$((KEEP_RUNS + 1))"
        )
        if (( ${#_old_runs[@]} > 0 )); then
            printf 'Purge de %d run(s) au-delà des %d plus récents :\n' \
                "${#_old_runs[@]}" "$KEEP_RUNS"
            for _r in "${_old_runs[@]}"; do
                echo "  - $(basename "$_r")"
                rm -rf "$_r"
            done
        fi
    else
        echo "KEEP_RUNS='$KEEP_RUNS' ignoré (entier > 0 attendu)." >&2
    fi
fi

(( fail > 0 )) && exit 1
exit 0
