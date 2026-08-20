# Source de demo/example_help_page.png (déterministe : ni creds ni réseau).
#
# vhs v0.11.0 n'écrit PAS de PNG directement : on produit un GIF (net pour le
# texte coloré, sans bavure de compression) dont ffmpeg extrait une frame.
# Utilise le script qui automatise ça :
#   ./demo/build_demos.sh
# Haute résolution volontaire (suréchantillon) : la capture reste nette même
# réduite à l'affichage. Height doit couvrir TOUTE la page d'aide, sinon le
# terminal scrolle et la capture perd le haut (la ligne « Usage »). Compter
# ~1,18 x FontSize par ligne : 26 lignes de sortie + 3 de prompt à FontSize 26
# tiennent dans 980 px, padding compris. Revoir cette valeur si l'aide s'allonge.

Output demo/example_help_page.gif

Set Shell bash
Set FontSize 26
Set Width 1500
Set Height 980
Set Padding 24

# Active le venv et nettoie l'écran hors capture.
Hide
Type "source .venv/bin/activate"
Enter
Type "clear"
Enter
Show

Type "rip url --help"
Enter
Sleep 3s
