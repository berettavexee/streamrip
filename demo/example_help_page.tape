# Régénère demo/example_help_page.png
#
# Déterministe : n'exige NI credentials NI réseau.
# Usage (depuis la racine du repo, vhs installé) :
#   vhs demo/example_help_page.tape
#
# Si la page d'aide déborde (scroll), baisse FontSize ou augmente Height.

Output demo/example_help_page.png

Set Shell bash
Set FontSize 22
Set Width 1656
Set Height 1574
Set Padding 20

# Active le venv et nettoie l'écran sans que ça apparaisse dans la capture.
Hide
Type "source .venv/bin/activate"
Enter
Type "clear"
Enter
Show

Type "rip url --help"
Enter
Sleep 3s
