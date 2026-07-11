# Whitelist vulture — symboles signalés comme inutilisés mais volontairement
# conservés. Vulture parse ce fichier (sans l'exécuter) et considère tout nom
# référencé ici comme « utilisé », ce qui garde les analyses propres pour faire
# ressortir le vrai code mort futur.
#
# Usage :
#   vulture streamrip/ .vulture_whitelist.py --min-confidence 60
#
# Régénérer le point de départ après une revue :
#   vulture streamrip/ --make-whitelist --min-confidence 60
# puis ré-organiser/élaguer les entrées ci-dessous.
#
# Note : ce fichier contient des noms non définis à dessein (F821) ; il est
# exclu de ruff via pyproject.toml.

# ── Conservé pour la parité upstream (nathom/streamrip) ──────────────────────
# Méthodes Tidal/Qobuz non câblées dans ce fork Deezer, gardées pour limiter
# les conflits de merge avec l'upstream.
_.get_featured          # unused method (streamrip/client/qobuz.py:310)
_.get_user_playlists    # unused method (streamrip/client/qobuz.py:325)
_.get_video_file_url    # unused method (streamrip/client/tidal.py:322)
_._get_login_link       # unused method (streamrip/client/tidal.py:381)

# ── API publique / utilisée seulement par les tests ──────────────────────────
_.pipe_query            # unused method (streamrip/client/deezer.py:299)
_._album_cache          # unused property — alias de compat pour les tests (deezer.py:198)
_._album_tasks          # unused property — alias de compat pour les tests (deezer.py:202)
_.reset                 # unused method — testé (streamrip/db.py:187)
_.get_failed_downloads  # unused method — testé (streamrip/db.py:254)
_._non_albums           # unused method (streamrip/media/artist.py:158)
_.defaults              # unused method — Config.defaults() (streamrip/config.py:313, 398)
__init__                # unused function (streamrip/config.py:362)
get_soundcloud_id       # unused function (streamrip/metadata/playlist.py:15)
parse_soundcloud_id     # unused function (streamrip/metadata/playlist.py:38)

# ── Commandes CLI click (enregistrées par décorateur) ────────────────────────
config_open             # unused function (streamrip/rip/cli.py:345)
config_reset            # unused function (streamrip/rip/cli.py:365)
database_browse         # unused function (streamrip/rip/cli.py:395)

# ── Champs de config désérialisés depuis TOML (Config(**toml[...])) ───────────
download_booklets       # unused variable (streamrip/config.py:37)
download_videos         # unused variable (streamrip/config.py:56)
use_deezloader          # unused variable (streamrip/config.py:75)
deezloader_warnings     # unused variable (streamrip/config.py:78)
lossy_bitrate           # unused variable (streamrip/config.py:112)
non_albums              # unused variable (streamrip/config.py:122)
exclude                 # unused variable (streamrip/config.py:159)
text_output             # unused variable (streamrip/config.py:224)
max_search_results      # unused variable (streamrip/config.py:228)

# ── Attributs de classe utilisés en dispatch polymorphe ──────────────────────
max_quality             # unused variable (client/{client,deezer,qobuz,tidal}.py)
codec_name              # unused variable (streamrip/converter.py, classes codec)
_.encoding              # unused attribute (streamrip/client/tidal.py:345)

# ── Champs de métadonnées consommés par le tagger ────────────────────────────
comment                 # unused variable (streamrip/metadata/album.py:44)
compilation             # unused variable (streamrip/metadata/album.py:45)
encoder                 # unused variable (streamrip/metadata/album.py:49)
grouping                # unused variable (streamrip/metadata/album.py:50)
purchase_date           # unused variable (streamrip/metadata/album.py:52)

# ── Paramètres de méthodes @abstractmethod (contrat d'interface) ─────────────
kvs                     # unused variable (streamrip/db.py:41, 50)

# ── Constantes de chemins (alias module) ─────────────────────────────────────
CACHE_DIR               # unused variable (streamrip/rip/user_paths.py:10)
DOWNLOADS_DIR           # unused variable (streamrip/rip/user_paths.py:13)
