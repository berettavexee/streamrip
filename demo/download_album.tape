# Source de demo/download_album.png.
#
# EXIGE : un compte configuré (ARL Deezer dans config.toml) + réseau.
# vhs produit un GIF (texte net, sans bavure de compression) ; ffmpeg en extrait
# une frame PENDANT le téléchargement (barres de progression Rich actives). Le
# script ./demo/build_demos.sh extrait la dernière frame : comme la vidéo se
# termine avant la fin d'un album complet, les barres sont encore actives.
# Remplace l'URL par un album réel de ton choix.

Output demo/download_album.gif

Set Shell bash
Set FontSize 26
Set Width 1500
# Hauteur ajustée au contenu : titre + « Overall » + une barre par
# téléchargement concurrent (max_connections, 6 par défaut) = 8 lignes. Plus
# haut, la capture est à moitié vide dans le README. À revoir si tu montes
# max_connections.
Set Height 460
Set Padding 24

Hide
Type "source .venv/bin/activate"
Enter
Type "clear"
Enter
Show

Type "rip -ndb url 'https://www.deezer.com/album/302127'"
Enter
# Laisse la résolution + les barres de progression apparaître, SANS attendre la
# fin : l'album entier descend en ~18 s depuis que la résolution des URL se fait
# par lot, et une attente plus longue ne capturerait que le résumé sur un écran
# vide. Si la capture montre « ✔ N tracks », c'est que cette valeur est trop
# haute.
Sleep 8s
