# Source de demo/download_album.png.
#
# EXIGE : un compte configuré (ARL Deezer dans config.toml) + réseau.
# vhs produit un .mp4 ; ffmpeg en extrait une frame PENDANT le
# téléchargement (barres de progression Rich actives). Le script
# ./demo/build_demos.sh extrait la dernière frame : comme la vidéo se
# termine à ~Sleep 6s (< durée d'un album complet), les barres sont
# encore actives. Remplace l'URL par un album réel de ton choix.

Output demo/download_album.mp4

Set Shell bash
Set FontSize 22
Set Width 1618
Set Height 1088
Set Padding 20

Hide
Type "source .venv/bin/activate"
Enter
Type "clear"
Enter
Show

Type "rip url 'https://www.deezer.com/album/302127'"
Enter
# Laisse la résolution + les premières barres de progression apparaître.
Sleep 6s
