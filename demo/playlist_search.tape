# Source de demo/playlist_search.png.
#
# EXIGE : la source (ici « tidal ») configurée dans config.toml + réseau.
# vhs produit un GIF (texte net, sans bavure de compression) ; ffmpeg en extrait
# la dernière frame (la liste interactive à l'écran). Voir ./demo/build_demos.sh.
# Pour ce fork orienté Deezer, tu peux remplacer « tidal » par « deezer » —
# garde la commande cohérente avec le texte du README.

Output demo/playlist_search.gif

Set Shell bash
Set FontSize 26
Set Width 1500
Set Height 760
Set Padding 24

Hide
Type "source .venv/bin/activate"
Enter
Type "clear"
Enter
Show

Type "rip search deezer playlist 'bossa nova'"
Enter
# Laisse les résultats s'afficher avant la fin de la vidéo.
Sleep 4s
