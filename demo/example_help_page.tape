# Source de demo/example_help_page.png (déterministe : ni creds ni réseau).
#
# vhs v0.11.0 n'écrit PAS de PNG directement (Output *.png et Screenshot
# sont silencieusement ignorés) : on produit un .mp4 dont ffmpeg extrait
# une frame. Utilise le script qui automatise ça :
#   ./demo/build_demos.sh
# Si la page d'aide déborde (scroll), baisse FontSize ou augmente Height.

Output demo/example_help_page.mp4

Set Shell bash
Set FontSize 22
Set Width 1656
Set Height 800
Set Padding 20

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
