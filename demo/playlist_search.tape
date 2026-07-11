# Régénère demo/playlist_search.png
#
# EXIGE : la source (ici « tidal ») configurée dans config.toml, + réseau.
# UI interactive : on tape la requête, on attend l'affichage des résultats,
# puis vhs capture la dernière frame (la liste interactive à l'écran).
# Usage :
#   vhs demo/playlist_search.tape
#
# Pour ce fork orienté Deezer, tu peux remplacer « tidal » par « deezer ».
# Garde la commande cohérente avec le texte du README (ligne « rip search … »).

Output demo/playlist_search.png

Set Shell bash
Set FontSize 22
Set Width 1726
Set Height 1238
Set Padding 20

Hide
Type "source .venv/bin/activate"
Enter
Type "clear"
Enter
Show

Type "rip search tidal playlist 'rap'"
Enter
# Laisse le temps aux résultats de s'afficher avant la capture.
Sleep 4s
